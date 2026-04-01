"""逆引きインデックスビルダー

screen_to_source 結果から partial_file / helper_method / model_name の逆引きインデックスを構築する。
インデックスは `.rails-lens/cache/screen_reverse_index.json` にキャッシュされ、
以下ファイルの mtime 変更で自動的に無効化される:
  - config/routes.rb
  - config/routes/*.rb
  - app/views/**/*
  - app/helpers/**/*
  - config/locales/**/*
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rails_lens.analyzers.template_parser import TemplateParser
from rails_lens.analyzers.view_resolver import PartialNode, ViewResolver
from rails_lens.config import RailsLensConfig

logger = logging.getLogger(__name__)

# ============================================================
# データ構造
# ============================================================

ScreenRefDict = dict[str, Any]
"""
{
  screen_name: str,
  controller_action: str | None,
  url_pattern: str | None,
  included_via: str | None,    # "path/to/template:LINE"
  via_partial: bool,
  is_api: bool,
  attributes_used: list[str],  # モデル参照のみ
  methods_used: list[str],     # モデル参照のみ
}
"""


@dataclass
class ReverseIndex:
    """逆引きインデックスのデータ構造"""

    # partial_file (例: "app/views/shared/_nav.html.erb") → [screen_refs]
    partials: dict[str, list[ScreenRefDict]] = field(default_factory=dict)
    # layout_file → [screen_refs] (via_layout=True として扱う)
    layouts: dict[str, list[ScreenRefDict]] = field(default_factory=dict)
    # helper_method_name → [screen_refs]
    helpers: dict[str, list[ScreenRefDict]] = field(default_factory=dict)
    # ModelName → [screen_refs]
    models: dict[str, list[ScreenRefDict]] = field(default_factory=dict)


# ============================================================
# ビルダー
# ============================================================


def _variable_to_model_name(variable: str) -> str:
    """@user → User, @blog_post → BlogPost"""
    name = variable.lstrip("@")
    return "".join(part.capitalize() for part in name.split("_"))


def _find_layout_file(layout_name: str, project_root: Path) -> str | None:
    """レイアウト名からファイルパスを解決する"""
    layouts_dir = project_root / "app" / "views" / "layouts"
    for ext in (".html.erb", ".html.haml", ".html.slim", ".erb", ".haml", ".slim"):
        candidate = layouts_dir / (layout_name + ext)
        if candidate.exists():
            try:
                return str(candidate.relative_to(project_root))
            except ValueError:
                return str(candidate)
    return None


def _index_partial_nodes(
    nodes: list[PartialNode],
    screen_ref_base: ScreenRefDict,
    index: ReverseIndex,
) -> None:
    """PartialNode ツリーを再帰的にインデックスへ追加する"""
    for node in nodes:
        partial_file = node.file
        if partial_file:
            ref: ScreenRefDict = {
                **screen_ref_base,
                "included_via": node.called_from,
                "via_partial": True,
                "is_api": screen_ref_base.get("is_api", False),
                "attributes_used": [],
                "methods_used": [],
            }
            index.partials.setdefault(partial_file, []).append(ref)
        _index_partial_nodes(node.nested_partials, screen_ref_base, index)


class ReverseIndexBuilder:
    """screen_to_source 結果から逆引きインデックスを構築・キャッシュ管理するクラス"""

    CACHE_FILE_NAME = "screen_reverse_index.json"

    # mtime 監視パターン
    WATCH_PATTERNS = [
        "config/routes.rb",
        "config/routes/*.rb",
        "app/views/**/*",
        "app/helpers/**/*",
        "config/locales/**/*",
    ]

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self._project_root = Path(config.rails_project_path)
        self._cache_file = self._project_root / config.cache_directory / self.CACHE_FILE_NAME

    # ----------------------------------------------------------
    # キャッシュ管理
    # ----------------------------------------------------------

    def load_cache(self) -> ReverseIndex | None:
        """キャッシュからインデックスを読み込む。staleなら None を返す。"""
        if not self._cache_file.is_file():
            return None

        try:
            with open(self._cache_file, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Reverse index cache read error: %s", e)
            return None

        # mtime 比較
        saved_snapshot = data.get("_mtime_snapshot", {})
        current_snapshot = self._take_mtime_snapshot()
        if saved_snapshot != current_snapshot:
            logger.info("Reverse index cache invalidated (mtime changed)")
            self._cache_file.unlink(missing_ok=True)
            return None

        logger.debug("Reverse index cache hit")
        return self._from_dict(data.get("index", {}))

    def save_cache(self, index: ReverseIndex) -> None:
        """インデックスをキャッシュファイルに保存する。"""
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_mtime_snapshot": self._take_mtime_snapshot(),
            "index": self._to_dict(index),
        }
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.debug("Reverse index cache saved: %s", self._cache_file)

    def _take_mtime_snapshot(self) -> dict[str, float]:
        """監視対象ファイルの mtime スナップショットを取得する。"""
        snapshot: dict[str, float] = {}
        for pattern in self.WATCH_PATTERNS:
            for path in self._project_root.glob(pattern):
                if path.is_file():
                    try:
                        rel = str(path.relative_to(self._project_root))
                    except ValueError:
                        rel = str(path)
                    snapshot[rel] = path.stat().st_mtime
        return snapshot

    # ----------------------------------------------------------
    # インデックス構築
    # ----------------------------------------------------------

    def build_from_mappings(
        self, mappings: list[dict[str, Any]]
    ) -> ReverseIndex:
        """dump_view_mapping.rb の "all" 結果から逆引きインデックスを構築する。

        静的解析（ViewResolver + TemplateParser）を使用するため bridge 不要。
        """
        index = ReverseIndex()
        resolver = ViewResolver(self.config)
        parser = TemplateParser(self.config)

        for mapping in mappings:
            ctrl = mapping.get("controller", "")
            action = mapping.get("action", "")
            url_pattern = mapping.get("path", "")
            controller_action = _build_controller_action(ctrl, action)
            layout_name = mapping.get("layout")
            conventional_template = mapping.get("conventional_template", "")
            explicit_render = mapping.get("explicit_render")

            # is_api: テンプレートを持たないJSONレスポンス専用ルートと判定
            is_api = _is_api_route(mapping)

            screen_ref_base: ScreenRefDict = {
                "screen_name": mapping.get("screen_name", controller_action),
                "controller_action": controller_action,
                "url_pattern": url_pattern,
                "included_via": None,
                "via_partial": False,
                "is_api": is_api,
                "attributes_used": [],
                "methods_used": [],
            }

            # ---- Layout ----
            if layout_name:
                layout_file = _find_layout_file(layout_name, self._project_root)
                if layout_file:
                    index.layouts.setdefault(layout_file, []).append(screen_ref_base.copy())

            # ---- Template ----
            template_rel: str | None = None
            if explicit_render and "/" in explicit_render:
                parts = explicit_render.split("/", 1)
                template_rel = resolver.find_template(parts[0], parts[1])
            if template_rel is None and conventional_template:
                parts = conventional_template.split("/", 1)
                if len(parts) == 2:
                    template_rel = resolver.find_template(parts[0], parts[1])
            if template_rel is None and ctrl and action:
                template_rel = resolver.find_template(ctrl, action)

            if template_rel is None:
                continue

            # ---- Partials ----
            partial_nodes = resolver.resolve_partials(template_rel)
            _index_partial_nodes(partial_nodes, screen_ref_base, index)

            # ---- Helpers ----
            try:
                analysis = parser.parse(template_rel)
            except Exception as e:
                logger.debug("Template parse error %s: %s", template_rel, e)
                continue

            for h in analysis.helpers:
                helper_ref: ScreenRefDict = {
                    **screen_ref_base,
                    "included_via": f"{template_rel}:{h.line}",
                    "via_partial": False,
                    "attributes_used": [],
                    "methods_used": [],
                }
                index.helpers.setdefault(h.method, []).append(helper_ref)

            # ---- Models ----
            model_map: dict[str, dict[str, list[str]]] = {}
            for ref in analysis.model_refs:
                model_name = _variable_to_model_name(ref.variable)
                if model_name not in model_map:
                    model_map[model_name] = {"attrs": [], "methods": []}
                model_map[model_name]["attrs"].append(ref.attribute)

            for model_name, data in model_map.items():
                model_ref: ScreenRefDict = {
                    **screen_ref_base,
                    "via_partial": False,
                    "attributes_used": list(set(data["attrs"])),
                    "methods_used": [],
                }
                index.models.setdefault(model_name, []).append(model_ref)

        return index

    # ----------------------------------------------------------
    # 静的 grep フォールバック（bridge 不可時）
    # ----------------------------------------------------------

    def build_partial_index_by_grep(self, partial_file: str) -> list[ScreenRefDict]:
        """grep でパーシャルを参照しているテンプレートを探し、簡易 screen_refs を返す。"""
        # partial_file 例: "app/views/shared/_navigation.html.erb"
        partial_path = Path(partial_file)
        # render で参照されるキー候補（"shared/navigation" など）
        # .html.erb のような複合拡張子を全て除去する
        partial_name = partial_path.name
        for ext in (".html.erb", ".html.haml", ".html.slim", ".erb", ".haml", ".slim"):
            if partial_name.endswith(ext):
                partial_name = partial_name[: -len(ext)]
                break
        partial_stem = partial_name.lstrip("_")
        parent_dir = partial_path.parent.name
        render_key = f"{parent_dir}/{partial_stem}"

        results: list[ScreenRefDict] = []
        views_dir = self._project_root / "app" / "views"
        if not views_dir.exists():
            return results

        pattern = re.compile(
            rf"""render\s+(?:partial:\s*)?['"]({re.escape(render_key)}|{re.escape(partial_stem)})['"]""",
            re.IGNORECASE,
        )
        for tpl in views_dir.rglob("*"):
            if not tpl.is_file():
                continue
            if tpl.suffix not in {".erb", ".haml", ".slim"}:
                continue
            try:
                content = tpl.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in pattern.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                try:
                    rel = str(tpl.relative_to(self._project_root))
                except ValueError:
                    rel = str(tpl)
                controller_action = _template_to_controller_action(rel)
                results.append({
                    "screen_name": controller_action or rel,
                    "controller_action": controller_action,
                    "url_pattern": None,
                    "included_via": f"{rel}:{line_no}",
                    "via_partial": True,
                    "is_api": False,
                    "attributes_used": [],
                    "methods_used": [],
                })
        return results

    def build_helper_index_by_grep(self, method_name: str) -> list[ScreenRefDict]:
        """grep でヘルパーメソッドを参照しているテンプレートを探す。"""
        results: list[ScreenRefDict] = []
        views_dir = self._project_root / "app" / "views"
        if not views_dir.exists():
            return results

        pattern = re.compile(rf"""\b{re.escape(method_name)}\s*[\(\s]""")
        for tpl in views_dir.rglob("*"):
            if not tpl.is_file():
                continue
            if tpl.suffix not in {".erb", ".haml", ".slim"}:
                continue
            try:
                content = tpl.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in pattern.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                try:
                    rel = str(tpl.relative_to(self._project_root))
                except ValueError:
                    rel = str(tpl)
                controller_action = _template_to_controller_action(rel)
                results.append({
                    "screen_name": controller_action or rel,
                    "controller_action": controller_action,
                    "url_pattern": None,
                    "included_via": f"{rel}:{line_no}",
                    "via_partial": not rel.startswith("app/views/layouts/"),
                    "is_api": False,
                    "attributes_used": [],
                    "methods_used": [],
                })
        return results

    def build_model_index_by_grep(self, model_name: str) -> list[ScreenRefDict]:
        """grep でモデルクラス名を参照しているテンプレートを探す。"""
        # @user / @users などの変数名を生成
        snake = re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), model_name).lstrip("_")
        results: list[ScreenRefDict] = []
        views_dir = self._project_root / "app" / "views"
        if not views_dir.exists():
            return results

        pattern = re.compile(rf"""@{re.escape(snake)}[s]?\.""", re.IGNORECASE)
        for tpl in views_dir.rglob("*"):
            if not tpl.is_file():
                continue
            if tpl.suffix not in {".erb", ".haml", ".slim"}:
                continue
            try:
                content = tpl.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern.search(content):
                try:
                    rel = str(tpl.relative_to(self._project_root))
                except ValueError:
                    rel = str(tpl)
                controller_action = _template_to_controller_action(rel)
                results.append({
                    "screen_name": controller_action or rel,
                    "controller_action": controller_action,
                    "url_pattern": None,
                    "included_via": None,
                    "via_partial": False,
                    "is_api": False,
                    "attributes_used": [],
                    "methods_used": [],
                })
        return results

    # ----------------------------------------------------------
    # シリアライズ
    # ----------------------------------------------------------

    def _to_dict(self, index: ReverseIndex) -> dict[str, Any]:
        return {
            "partials": index.partials,
            "layouts": index.layouts,
            "helpers": index.helpers,
            "models": index.models,
        }

    def _from_dict(self, data: dict[str, Any]) -> ReverseIndex:
        index = ReverseIndex()
        index.partials = data.get("partials", {})
        index.layouts = data.get("layouts", {})
        index.helpers = data.get("helpers", {})
        index.models = data.get("models", {})
        return index


# ============================================================
# ユーティリティ
# ============================================================


def _build_controller_action(controller: str, action: str) -> str:
    """controller="users" action="show" → "UsersController#show"
       controller="admin/users" action="index" → "Admin::UsersController#index"
    """
    if not controller or not action:
        return f"{controller}#{action}" if controller else action
    parts = controller.split("/")
    class_parts = ["".join(p.capitalize() for p in part.split("_")) for part in parts]
    class_name = "::".join(class_parts) + "Controller"
    return f"{class_name}#{action}"


def _is_api_route(mapping: dict[str, Any]) -> bool:
    """ルーティングが API 専用（JSON only）かどうかを判定する。"""
    constraint = mapping.get("format_constraint", "")
    if isinstance(constraint, str) and "json" in constraint.lower():
        return True
    path = mapping.get("path", "")
    return "/api/" in path or path.startswith("/api/")


def _template_to_controller_action(rel_path: str) -> str | None:
    """app/views/users/show.html.erb → UsersController#show

    パーシャル (_name.html.erb) やレイアウトはスキップ。
    """
    # rel_path 例: "app/views/users/show.html.erb"
    parts = rel_path.split("/")
    # parts = ["app", "views", "users", "show.html.erb"]
    if len(parts) < 4:
        return None
    if parts[0] != "app" or parts[1] != "views":
        return None

    # ファイル名
    filename = parts[-1]
    if filename.startswith("_"):
        return None  # partial

    # アクション名: strip extensions
    action = re.sub(r"\..+$", "", filename)

    # コントローラパス
    namespace_parts = parts[2:-1]
    if not namespace_parts or namespace_parts[0] == "layouts":
        return None

    class_parts = [
        "".join(p.capitalize() for p in part.split("_")) for part in namespace_parts
    ]
    ctrl_class = "::".join(class_parts) + "Controller"
    return f"{ctrl_class}#{action}"

"""rails_lens_screen_map ツール（Phase H-2/H-3/H-4: 双方向マッピング + full_inventory）"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from rails_lens.analyzers.api_detector import (
    detect_serializer,
    is_api_controller,
    is_json_only_action,
)
from rails_lens.analyzers.inventory_formatter import InventoryFormatter
from rails_lens.analyzers.reverse_index_builder import (
    ReverseIndex,
    ReverseIndexBuilder,
    _build_controller_action,
    _is_api_route,
)
from rails_lens.analyzers.screen_name_resolver import ScreenNameResolver, parse_controller_action
from rails_lens.analyzers.template_parser import TemplateParser
from rails_lens.analyzers.view_resolver import PartialNode, ViewResolver
from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse
from rails_lens.tools.get_routes import _fallback_get_routes

# ============================================================
# 入力スキーマ
# ============================================================


class ScreenMapMode(StrEnum):
    SCREEN_TO_SOURCE = "screen_to_source"
    SOURCE_TO_SCREENS = "source_to_screens"
    FULL_INVENTORY = "full_inventory"


class ScreenMapGroupBy(StrEnum):
    NAMESPACE = "namespace"
    RESOURCE = "resource"
    FLAT = "flat"


class ScreenMapInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mode: ScreenMapMode = Field(
        ...,
        description=(
            "実行モード: screen_to_source（画面→ソース）, "
            "source_to_screens（ソース→画面）, full_inventory（画面台帳）"
        ),
    )

    # screen_to_source 用
    url: str | None = Field(
        default=None,
        description="画面のURLパス (例: '/users/123')",
        max_length=500,
    )
    controller_action: str | None = Field(
        default=None,
        description="コントローラ#アクション (例: 'UsersController#show')",
        max_length=200,
    )

    # source_to_screens 用
    file_path: str | None = Field(
        default=None,
        description="ソースファイルのパス",
        max_length=500,
    )
    method_name: str | None = Field(
        default=None,
        description="ヘルパーメソッド名",
        max_length=200,
    )

    # full_inventory 用
    format: str | None = Field(
        default="json",
        description="出力形式: 'json' or 'markdown'",
    )
    include_api: bool | None = Field(
        default=True,
        description="APIエンドポイントも含めるか",
    )
    group_by: ScreenMapGroupBy | None = Field(
        default=ScreenMapGroupBy.NAMESPACE,
        description="グルーピング方法",
    )
    locale: str | None = Field(
        default="ja",
        description="画面名推定の言語",
    )


# ============================================================
# 出力 Pydantic モデル
# ============================================================


class ScreenInfo(BaseModel):
    url_pattern: str
    http_method: str
    controller_action: str
    screen_name: str
    screen_name_source: str


class LayoutInfo(BaseModel):
    file: str
    content_for_blocks: list[str] = Field(default_factory=list)


class TemplateInfo(BaseModel):
    file: str
    explicitly_specified: bool = False


class PartialInfo(BaseModel):
    name: str
    file: str
    called_from: str
    locals_passed: list[str] = Field(default_factory=list)
    collection: bool = False
    note: str = ""
    nested_partials: list[PartialInfo] = Field(default_factory=list)


class HelperUsage(BaseModel):
    method: str
    file: str
    line: int
    called_from: str


class DecoratorPresenterUsage(BaseModel):
    class_name: str
    file: str
    methods_used: list[str] = Field(default_factory=list)


class ModelReference(BaseModel):
    model: str
    attributes_accessed: list[str] = Field(default_factory=list)
    associations_accessed: list[str] = Field(default_factory=list)
    methods_called: list[str] = Field(default_factory=list)


class I18nKeyUsage(BaseModel):
    key: str
    value: str
    file: str


class HardcodedText(BaseModel):
    text: str
    file: str
    line: int


class AssetInfo(BaseModel):
    stylesheets: list[str] = Field(default_factory=list)
    javascripts: list[str] = Field(default_factory=list)
    stimulus_controllers: list[str] = Field(default_factory=list)


class ScreenToSourceOutput(BaseModel):
    screen: ScreenInfo
    layout: LayoutInfo | None = None
    template: TemplateInfo
    partials: list[PartialInfo] = Field(default_factory=list)
    helpers_used: list[HelperUsage] = Field(default_factory=list)
    decorators_presenters: list[DecoratorPresenterUsage] = Field(default_factory=list)
    models_referenced: list[ModelReference] = Field(default_factory=list)
    i18n_keys: list[I18nKeyUsage] = Field(default_factory=list)
    hardcoded_text: list[HardcodedText] = Field(default_factory=list)
    assets: AssetInfo = Field(default_factory=AssetInfo)
    _metadata: dict[str, str] | None = None


# ---- source_to_screens 出力モデル ----


class ScreenReference(BaseModel):
    screen_name: str
    controller_action: str | None = None
    url_pattern: str | None = None
    included_via: str | None = None
    inclusion_chain: list[str] = Field(default_factory=list)
    via_partial: bool = False
    is_api: bool = False
    note: str = ""
    attributes_used: list[str] = Field(default_factory=list)
    methods_used: list[str] = Field(default_factory=list)


class MethodScreenMapping(BaseModel):
    method_name: str
    line: int
    used_in_screens: list[ScreenReference] = Field(default_factory=list)
    total_screen_count: int = 0
    impact_level: str = "low"


class SourceToScreensOutput(BaseModel):
    source_file: str
    source_type: str  # "partial", "helper", "model", "decorator", "presenter"
    used_in_screens: list[ScreenReference] = Field(default_factory=list)
    methods: list[MethodScreenMapping] = Field(default_factory=list)
    total_screen_count: int | str = 0
    impact_level: str = "low"
    _metadata: dict[str, str] | None = None


# ---- full_inventory 出力モデル ----


class ScreenEntry(BaseModel):
    screen_name: str
    url_pattern: str
    http_method: str
    controller_action: str
    template: str | None = None
    partial_count: int = 0
    models: list[str] = Field(default_factory=list)
    is_api: bool = False
    serializer: str | None = None


class SharedPartialEntry(BaseModel):
    file: str
    screen_count: int | str = 0
    impact_level: str = "low"


class ScreenGroup(BaseModel):
    group_name: str
    screens: list[ScreenEntry] = Field(default_factory=list)


class FullInventoryOutput(BaseModel):
    generated_at: str
    total_screen_count: int = 0
    web_screen_count: int = 0
    api_endpoint_count: int = 0
    groups: list[ScreenGroup] = Field(default_factory=list)
    shared_partials: list[SharedPartialEntry] = Field(default_factory=list)
    markdown: str | None = None
    _metadata: dict[str, str] | None = None


# ============================================================
# 内部ヘルパー関数
# ============================================================

# i18n キー抽出パターン (t("key") / t(:symbol) / I18n.t("key"))
_I18N_T_PATTERN = re.compile(
    r"""(?:I18n\.)?t\s*\(\s*['"]([a-z_][a-z0-9_.]+)['"]""", re.MULTILINE
)

# content_for ブロック検出
_CONTENT_FOR_BLOCK = re.compile(r"""content_for\s+:(\w+)""", re.MULTILINE)


def _partial_node_to_info(node: PartialNode) -> PartialInfo:
    """PartialNode → PartialInfo 変換"""
    return PartialInfo(
        name=node.name,
        file=node.file,
        called_from=node.called_from,
        locals_passed=node.locals_passed,
        collection=node.collection,
        note=node.note,
        nested_partials=[_partial_node_to_info(c) for c in node.nested_partials],
    )


def _find_helper_file(
    method_name: str, project_root: Path
) -> tuple[str, int]:
    """ヘルパーディレクトリからメソッド定義のファイルと行番号を探す。"""
    helpers_dir = project_root / "app" / "helpers"
    if not helpers_dir.exists():
        return "unknown", 0

    pattern = re.compile(rf"^\s*def\s+{re.escape(method_name)}\b")
    for helper_file in sorted(helpers_dir.rglob("*.rb")):
        try:
            lines = helper_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if pattern.match(line):
                try:
                    rel = str(helper_file.relative_to(project_root))
                except ValueError:
                    rel = str(helper_file)
                return rel, line_no
    return "unknown", 0


def _variable_to_model_name(variable: str) -> str:
    """@user → User, @blog_post → BlogPost"""
    # Remove @ prefix, split by _, capitalize each part
    name = variable.lstrip("@")
    return "".join(part.capitalize() for part in name.split("_"))


def _detect_assets(
    controller: str, project_root: Path, stimulus_controllers: list[str]
) -> AssetInfo:
    """スタイルシート・JavaScript・Stimulusコントローラを検出する。"""
    stylesheets: list[str] = []
    javascripts: list[str] = []

    # controller: "users" or "admin/users"
    ctrl_name = controller.split("/")[-1]  # "users"

    # stylesheet candidates
    for ext in (".scss", ".css", ".sass"):
        for base in ("app/assets/stylesheets", "app/assets/stylesheets/screens"):
            candidate = project_root / base / (ctrl_name + ext)
            if candidate.exists():
                try:
                    stylesheets.append(str(candidate.relative_to(project_root)))
                except ValueError:
                    stylesheets.append(str(candidate))

    # javascript candidates
    for name in (
        f"controllers/{ctrl_name}_controller.js",
        f"controllers/{ctrl_name}_controller.ts",
        f"{ctrl_name}.js",
    ):
        candidate = project_root / "app" / "javascript" / name
        if candidate.exists():
            try:
                javascripts.append(str(candidate.relative_to(project_root)))
            except ValueError:
                javascripts.append(str(candidate))

    return AssetInfo(
        stylesheets=stylesheets,
        javascripts=javascripts,
        stimulus_controllers=stimulus_controllers,
    )


def _scan_i18n_keys(
    template_content: str,
    template_file: str,
    bridge_i18n: dict[str, str],
) -> list[I18nKeyUsage]:
    """テンプレートからi18nキーを抽出し、値を解決する。"""
    result: list[I18nKeyUsage] = []
    seen: set[str] = set()

    # bridge から提供された title 系キーを先に追加
    for key, value in bridge_i18n.items():
        if key not in seen:
            seen.add(key)
            result.append(I18nKeyUsage(key=key, value=value, file="(i18n runtime)"))

    # テンプレート内の t("key") パターン
    for m in _I18N_T_PATTERN.finditer(template_content):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            result.append(I18nKeyUsage(key=key, value="", file=template_file))

    return result


def _find_decorator_presenter(
    controller: str, project_root: Path, template_analysis_files: list[str]
) -> list[DecoratorPresenterUsage]:
    """コントローラリソースに関連するデコレータ・プレゼンタを検出する。"""
    result: list[DecoratorPresenterUsage] = []
    ctrl_name = controller.split("/")[-1]  # "users" → singular candidate

    # resource singular guess: "users" → "user"
    singulars = [ctrl_name]
    if ctrl_name.endswith("ies"):
        singulars.append(ctrl_name[:-3] + "y")
    elif ctrl_name.endswith("s") and not ctrl_name.endswith("ss"):
        singulars.append(ctrl_name[:-1])

    # CamelCase variants to match
    camel_variants = set()
    for s in singulars:
        camel_variants.add("".join(p.capitalize() for p in s.split("_")))

    for dir_name in ("decorators", "presenters"):
        dir_path = project_root / "app" / dir_name
        if not dir_path.exists():
            continue
        for rb_file in sorted(dir_path.rglob("*.rb")):
            try:
                rel = str(rb_file.relative_to(project_root))
            except ValueError:
                rel = str(rb_file)
            try:
                content = rb_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Check if any camel variant matches the class name
            for variant in camel_variants:
                if re.search(rf"\bclass\s+{re.escape(variant)}(?:Decorator|Presenter)\b", content):
                    class_m = re.search(r"\bclass\s+(\w+)", content)
                    class_name = class_m.group(1) if class_m else rb_file.stem.title()
                    result.append(
                        DecoratorPresenterUsage(class_name=class_name, file=rel)
                    )
                    break

    return result


def _build_layout_info(
    layout_name: str | None, project_root: Path, template_content_by_file: dict[str, str]
) -> LayoutInfo | None:
    """レイアウト情報を構築する。"""
    if not layout_name:
        return None

    # Resolve layout file
    layout_file = None
    views_root = project_root / "app" / "views" / "layouts"
    for ext in (".html.erb", ".html.haml", ".html.slim", ".erb", ".haml", ".slim"):
        candidate = views_root / (layout_name + ext)
        if candidate.exists():
            try:
                layout_file = str(candidate.relative_to(project_root))
            except ValueError:
                layout_file = str(candidate)
            break

    if not layout_file:
        # Return with conventional path
        layout_file = f"app/views/layouts/{layout_name}.html.erb"

    # Detect content_for blocks in layout
    content_for_blocks: list[str] = []
    if layout_file in template_content_by_file:
        content = template_content_by_file[layout_file]
    else:
        abs_layout = project_root / layout_file
        if abs_layout.exists():
            try:
                content = abs_layout.read_text(encoding="utf-8", errors="replace")
                template_content_by_file[layout_file] = content
            except OSError:
                content = ""
        else:
            content = ""

    if content:
        seen_blocks: set[str] = set()
        for m in _CONTENT_FOR_BLOCK.finditer(content):
            block = m.group(1)
            if block not in seen_blocks:
                seen_blocks.add(block)
                content_for_blocks.append(block)

    return LayoutInfo(file=layout_file, content_for_blocks=content_for_blocks)


# ============================================================
# Fallback 実装（Ruby実行失敗時の静的ファイル解析）
# ============================================================


def _resolve_from_url_fallback(url: str, config: Any) -> str | None:
    """Rails runner不使用時のURL→コントローラ#アクション解決フォールバック。

    config/routes.rb をテキストパースしてURLパターンにマッチするCA文字列を返す。
    動的セグメント（:id等）を含むURLパターンにも対応する。
    """
    data = _fallback_get_routes(config)
    routes = data.get("routes", [])
    normalized = url.rstrip("/")

    for route in routes:
        path = route.get("path", "").rstrip("/")
        action_str = route.get("action", "")
        if not path or not action_str or "#" not in action_str:
            continue

        # :param セグメントを正規表現に変換（例: /projects/:id → /projects/[^/]+）
        escaped = re.escape(path)
        pattern = re.sub(r":[^/]+", r"[^/]+", escaped)
        if re.fullmatch(pattern, normalized):
            ctrl_str, action = action_str.split("#", 1)
            # snake_case/namespaced → CamelCaseController
            parts = ctrl_str.split("/")
            camel_parts = ["".join(p.capitalize() for p in part.split("_")) for part in parts]
            ctrl_class = "::".join(camel_parts) + "Controller"
            return f"{ctrl_class}#{action}"

    return None


def _fallback_screen_to_source(
    controller_action: str, config: Any
) -> ScreenToSourceOutput:
    """Ruby実行失敗時のファイルベースフォールバック。"""
    project_root = Path(config.rails_project_path)
    locale = "ja"

    resource, action, namespaces = parse_controller_action(controller_action)
    # Derive controller path (snake_case with namespace)
    ctrl_path = "/".join(
        [_to_snake(ns) for ns in namespaces] + [resource + "s"]
        if namespaces
        else [resource + "s"]
    )
    # Try to find template
    resolver = ViewResolver(config)
    template_rel = resolver.find_template(ctrl_path, action)
    if template_rel is None:
        # Try without plural
        template_rel = resolver.find_template(resource, action)

    # Screen name via restful convention only
    name_resolver = ScreenNameResolver(config)
    screen_name, screen_name_source = name_resolver.resolve(
        controller_action, locale=locale
    )

    # HTTP method guess
    method_map = {"index": "GET", "show": "GET", "new": "GET", "edit": "GET",
                  "create": "POST", "update": "PATCH", "destroy": "DELETE"}
    http_method = method_map.get(action, "GET")

    screen = ScreenInfo(
        url_pattern=f"/{ctrl_path.replace('_', '-')}",
        http_method=http_method,
        controller_action=controller_action,
        screen_name=screen_name,
        screen_name_source=screen_name_source,
    )

    partials: list[PartialInfo] = []
    helpers_used: list[HelperUsage] = []
    models_referenced: list[ModelReference] = []
    hardcoded_texts: list[HardcodedText] = []
    stimulus_controllers: list[str] = []
    template_content = ""

    if template_rel:
        # Parse template
        parser = TemplateParser(config)
        analysis = parser.parse(template_rel)
        template_content = (project_root / template_rel).read_text(
            encoding="utf-8", errors="replace"
        ) if (project_root / template_rel).exists() else ""

        # Partials
        nodes = resolver.resolve_partials(template_rel)
        partials = [_partial_node_to_info(n) for n in nodes]

        # Helpers
        for h in analysis.helpers:
            helper_file, helper_line = _find_helper_file(h.method, project_root)
            helpers_used.append(
                HelperUsage(
                    method=h.method,
                    file=helper_file,
                    line=helper_line,
                    called_from=f"{template_rel}:{h.line}",
                )
            )

        # Model refs (group by variable → model name)
        model_map: dict[str, ModelReference] = {}
        for ref in analysis.model_refs:
            model_name = _variable_to_model_name(ref.variable)
            if model_name not in model_map:
                model_map[model_name] = ModelReference(model=model_name)
            model_map[model_name].attributes_accessed.append(ref.attribute)
        models_referenced = list(model_map.values())

        # Hardcoded text
        for ht in analysis.hardcoded_text:
            hardcoded_texts.append(
                HardcodedText(text=ht.text, file=template_rel, line=ht.line)
            )

        stimulus_controllers = analysis.stimulus_controllers

    i18n_keys = _scan_i18n_keys(template_content, template_rel or "", {})
    assets = _detect_assets(ctrl_path, project_root, stimulus_controllers)
    decorators = _find_decorator_presenter(ctrl_path, project_root, [])

    output = ScreenToSourceOutput(
        screen=screen,
        layout=None,
        template=TemplateInfo(
            file=template_rel or f"app/views/{ctrl_path}/{action}.html.erb",
            explicitly_specified=False,
        ),
        partials=partials,
        helpers_used=helpers_used,
        decorators_presenters=decorators,
        models_referenced=models_referenced,
        i18n_keys=i18n_keys,
        hardcoded_text=hardcoded_texts,
        assets=assets,
    )
    output._metadata = {"source": "file_analysis", "note": "Rails runner unavailable"}
    return output


def _to_snake(name: str) -> str:
    """CamelCase → snake_case"""
    return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), name).lstrip("_")


# ============================================================
# メイン実装
# ============================================================


async def _screen_to_source_impl(
    params: ScreenMapInput,
    bridge: Any,
    config: Any,
) -> ScreenToSourceOutput:
    """screen_to_source モードのメイン処理。"""
    project_root = Path(config.rails_project_path)
    locale = params.locale or "ja"

    # controller_action の決定 (controller_action優先、urlから解決)
    controller_action = params.controller_action
    if not controller_action and params.url:
        controller_action = await _resolve_from_url(params.url, bridge)
        if not controller_action:
            # bridge失敗時はroutes.rbテキストパースでフォールバック解決
            controller_action = _resolve_from_url_fallback(params.url, config)
    if not controller_action:
        raise ValueError(f"URLを解決できませんでした: {params.url}")

    # Bridge でルーティング・レイアウト・i18n情報を取得
    mapping: dict[str, Any] = {}
    is_fallback = False
    try:
        data = await bridge.execute(
            "dump_view_mapping.rb",
            args=["single", controller_action],
        )
        mappings = data.get("mappings", [])
        if mappings:
            mapping = mappings[0]
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        is_fallback = True

    if is_fallback:
        return _fallback_screen_to_source(controller_action, config)

    # ルーティング情報
    url_pattern = mapping.get("path", "")
    http_method = mapping.get("verb", "GET")
    controller = mapping.get("controller", "")  # "users" or "admin/users"
    action = mapping.get("action", "")
    layout_name = mapping.get("layout")
    i18n_title_keys: dict[str, str] = mapping.get("i18n_title_keys", {})
    conventional_template = mapping.get("conventional_template", "")  # "users/show"
    explicit_render = mapping.get("explicit_render")

    # テンプレートファイル特定
    resolver = ViewResolver(config)
    template_rel: str | None = None
    explicitly_specified = False

    if explicit_render:
        # 明示指定テンプレート
        if "/" in explicit_render:
            template_rel = resolver.find_template(*explicit_render.split("/", 1))
        explicitly_specified = True

    if template_rel is None and conventional_template:
        parts = conventional_template.split("/", 1)
        if len(parts) == 2:
            template_rel = resolver.find_template(parts[0], parts[1])
        elif parts:
            template_rel = resolver.find_template(controller, parts[0])

    if template_rel is None:
        template_rel = resolver.find_template(controller, action)

    # 画面名推定
    name_resolver = ScreenNameResolver(config)
    screen_name, screen_name_source = name_resolver.resolve(
        controller_action,
        i18n_keys=i18n_title_keys,
        template_path=template_rel,
        locale=locale,
    )

    screen = ScreenInfo(
        url_pattern=url_pattern,
        http_method=http_method,
        controller_action=controller_action,
        screen_name=screen_name,
        screen_name_source=screen_name_source,
    )

    # テンプレート解析
    parser = TemplateParser(config)
    template_content = ""
    analysis = None
    if template_rel:
        analysis = parser.parse(template_rel)
        abs_tpl = project_root / template_rel
        if abs_tpl.exists():
            with contextlib.suppress(OSError):
                template_content = abs_tpl.read_text(encoding="utf-8", errors="replace")

    # パーシャル再帰解決
    partials: list[PartialInfo] = []
    if template_rel:
        nodes = resolver.resolve_partials(template_rel)
        partials = [_partial_node_to_info(n) for n in nodes]

    # レイアウト情報
    template_content_cache: dict[str, str] = {}
    if template_rel and template_content:
        template_content_cache[template_rel] = template_content
    layout_info = _build_layout_info(layout_name, project_root, template_content_cache)

    # ヘルパー使用一覧
    helpers_used: list[HelperUsage] = []
    if analysis:
        for h in analysis.helpers:
            helper_file, helper_line = _find_helper_file(h.method, project_root)
            helpers_used.append(
                HelperUsage(
                    method=h.method,
                    file=helper_file,
                    line=helper_line,
                    called_from=f"{template_rel}:{h.line}",
                )
            )

    # デコレータ・プレゼンタ
    decorators = _find_decorator_presenter(controller, project_root, [])

    # モデル参照（@variable.attribute でグループ化）
    models_referenced: list[ModelReference] = []
    if analysis:
        model_map: dict[str, ModelReference] = {}
        for ref in analysis.model_refs:
            model_name = _variable_to_model_name(ref.variable)
            if model_name not in model_map:
                model_map[model_name] = ModelReference(model=model_name)
            model_map[model_name].attributes_accessed.append(ref.attribute)
        models_referenced = list(model_map.values())

    # i18n キー
    i18n_keys_list = _scan_i18n_keys(
        template_content, template_rel or "", i18n_title_keys
    )

    # ハードコードテキスト
    hardcoded_text: list[HardcodedText] = []
    if analysis and template_rel:
        for ht in analysis.hardcoded_text:
            hardcoded_text.append(
                HardcodedText(text=ht.text, file=template_rel, line=ht.line)
            )

    # アセット検出
    stimulus = analysis.stimulus_controllers if analysis else []
    assets = _detect_assets(controller, project_root, stimulus)

    return ScreenToSourceOutput(
        screen=screen,
        layout=layout_info,
        template=TemplateInfo(
            file=template_rel or f"app/views/{controller}/{action}.html.erb",
            explicitly_specified=explicitly_specified,
        ),
        partials=partials,
        helpers_used=helpers_used,
        decorators_presenters=decorators,
        models_referenced=models_referenced,
        i18n_keys=i18n_keys_list,
        hardcoded_text=hardcoded_text,
        assets=assets,
    )


# ============================================================
# source_to_screens 実装
# ============================================================


def _determine_source_type(file_path: str) -> str:
    """ファイルパスからソースタイプを判定する。"""
    fp = file_path.replace("\\", "/")
    if "/app/decorators/" in fp or fp.startswith("app/decorators/"):
        return "decorator"
    if "/app/presenters/" in fp or fp.startswith("app/presenters/"):
        return "presenter"
    if "/app/helpers/" in fp or fp.startswith("app/helpers/"):
        return "helper"
    if "/app/models/" in fp or fp.startswith("app/models/"):
        return "model"
    # ビューファイル: パーシャルは _ で始まる
    import os
    basename = os.path.basename(fp)
    if basename.startswith("_"):
        return "partial"
    # 一般テンプレート（非パーシャルビュー）もパーシャルとして扱う
    if "/app/views/" in fp or fp.startswith("app/views/"):
        return "partial"
    return "partial"


def _determine_impact_level(count: int | str, via_layout: bool = False) -> str:
    """impact_level を判定する。"""
    if via_layout:
        return "critical"
    if isinstance(count, str):
        return "critical"
    if count >= 10:
        return "critical"
    if count >= 5:
        return "high"
    if count >= 2:
        return "moderate"
    return "low"


def _screen_refs_from_index(refs: list[dict[str, Any]]) -> list[ScreenReference]:
    """逆引きインデックスの refs リストを ScreenReference リストに変換する。"""
    result = []
    for r in refs:
        result.append(ScreenReference(
            screen_name=r.get("screen_name", ""),
            controller_action=r.get("controller_action"),
            url_pattern=r.get("url_pattern"),
            included_via=r.get("included_via"),
            inclusion_chain=[r["included_via"]] if r.get("included_via") else [],
            via_partial=r.get("via_partial", False),
            is_api=r.get("is_api", False),
            note=r.get("note", ""),
            attributes_used=r.get("attributes_used", []),
            methods_used=r.get("methods_used", []),
        ))
    return result


async def _build_reverse_index(bridge: Any, config: Any) -> ReverseIndex:
    """bridge 経由で全ルーティングを取得し、逆引きインデックスを構築する。"""
    builder = ReverseIndexBuilder(config)

    # キャッシュ確認
    cached = builder.load_cache()
    if cached is not None:
        return cached

    # bridge から全ルーティング取得
    data = await bridge.execute("dump_view_mapping.rb", args=["all"])
    mappings = data.get("mappings", [])

    index = builder.build_from_mappings(mappings)
    builder.save_cache(index)
    return index


async def _source_to_screens_impl(
    params: ScreenMapInput,
    bridge: Any,
    config: Any,
) -> SourceToScreensOutput:
    """source_to_screens モードのメイン処理。"""
    file_path = params.file_path or ""
    source_type = _determine_source_type(file_path)
    builder = ReverseIndexBuilder(config)

    # Bridge でインデックス構築を試みる
    is_fallback = False
    index: ReverseIndex | None = None
    try:
        index = await _build_reverse_index(bridge, config)
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        is_fallback = True

    if is_fallback or index is None:
        return _fallback_source_to_screens(file_path, source_type, builder)

    return _source_to_screens_from_index(file_path, source_type, index, config, params)


def _source_to_screens_from_index(
    file_path: str,
    source_type: str,
    index: ReverseIndex,
    config: Any,
    params: ScreenMapInput,
) -> SourceToScreensOutput:
    """逆引きインデックスから SourceToScreensOutput を組み立てる。"""
    project_root = Path(config.rails_project_path)

    if source_type == "partial":
        # レイアウト経由かチェック
        layout_refs = index.layouts.get(file_path, [])
        if layout_refs:
            screens = _screen_refs_from_index(layout_refs)
            total: int | str = "all (layout)"
            impact = "critical"
        else:
            refs = index.partials.get(file_path, [])
            screens = _screen_refs_from_index(refs)
            total = len(screens)
            impact = _determine_impact_level(total)

        return SourceToScreensOutput(
            source_file=file_path,
            source_type=source_type,
            used_in_screens=screens,
            methods=[],
            total_screen_count=total,
            impact_level=impact,
        )

    if source_type == "helper":
        # ファイル内のメソッド定義を収集
        abs_path = project_root / file_path
        method_names: list[str] = []
        if abs_path.is_file():
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            method_names = re.findall(r"^\s*def\s+([a-z_][a-z0-9_?!]*)", content, re.MULTILINE)

        # 特定メソッド指定があればフィルタ
        if params.method_name:
            method_names = [m for m in method_names if m == params.method_name]

        method_mappings: list[MethodScreenMapping] = []
        all_screens_set: set[str] = set()
        for method_name in method_names:
            refs = index.helpers.get(method_name, [])
            screens = _screen_refs_from_index(refs)
            count = len(screens)
            method_mappings.append(MethodScreenMapping(
                method_name=method_name,
                line=_find_method_line(abs_path, method_name),
                used_in_screens=screens,
                total_screen_count=count,
                impact_level=_determine_impact_level(count),
            ))
            for s in screens:
                if s.controller_action:
                    all_screens_set.add(s.controller_action)

        total_count = len(all_screens_set)
        return SourceToScreensOutput(
            source_file=file_path,
            source_type="helper",
            used_in_screens=[],
            methods=method_mappings,
            total_screen_count=total_count,
            impact_level=_determine_impact_level(total_count),
        )

    if source_type in ("model", "decorator", "presenter"):
        # モデル名を推定（User, BlogPost など）
        model_name = _file_path_to_class_name(file_path)
        refs = index.models.get(model_name, [])
        screens = _screen_refs_from_index(refs)
        total_count = len(screens)
        return SourceToScreensOutput(
            source_file=file_path,
            source_type=source_type,
            used_in_screens=screens,
            methods=[],
            total_screen_count=total_count,
            impact_level=_determine_impact_level(total_count),
        )

    # 不明なタイプ
    return SourceToScreensOutput(
        source_file=file_path,
        source_type=source_type,
        used_in_screens=[],
        methods=[],
        total_screen_count=0,
        impact_level="low",
    )


def _fallback_source_to_screens(
    file_path: str,
    source_type: str,
    builder: ReverseIndexBuilder,
) -> SourceToScreensOutput:
    """bridge 失敗時の grep ベースフォールバック。"""
    if source_type == "partial":
        refs_raw = builder.build_partial_index_by_grep(file_path)
        screens = _screen_refs_from_index(refs_raw)
        total: int | str = len(screens)
        impact = _determine_impact_level(total)
    elif source_type == "helper":
        abs_path = builder._project_root / file_path
        method_names: list[str] = []
        if abs_path.is_file():
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            method_names = re.findall(r"^\s*def\s+([a-z_][a-z0-9_?!]*)", content, re.MULTILINE)
        method_mappings: list[MethodScreenMapping] = []
        all_screens_set: set[str] = set()
        for method_name in method_names:
            refs_raw = builder.build_helper_index_by_grep(method_name)
            screens_m = _screen_refs_from_index(refs_raw)
            method_mappings.append(MethodScreenMapping(
                method_name=method_name,
                line=_find_method_line(abs_path, method_name),
                used_in_screens=screens_m,
                total_screen_count=len(screens_m),
                impact_level=_determine_impact_level(len(screens_m)),
            ))
            for s in screens_m:
                if s.controller_action:
                    all_screens_set.add(s.controller_action)
        total_count = len(all_screens_set)
        output = SourceToScreensOutput(
            source_file=file_path,
            source_type="helper",
            used_in_screens=[],
            methods=method_mappings,
            total_screen_count=total_count,
            impact_level=_determine_impact_level(total_count),
        )
        output._metadata = {"source": "file_analysis", "note": "Rails runner unavailable"}
        return output
    else:
        model_name = _file_path_to_class_name(file_path)
        refs_raw = builder.build_model_index_by_grep(model_name)
        screens = _screen_refs_from_index(refs_raw)
        total = len(screens)
        impact = _determine_impact_level(total)

    output_base = SourceToScreensOutput(
        source_file=file_path,
        source_type=source_type,
        used_in_screens=screens if source_type != "helper" else [],
        methods=[],
        total_screen_count=total,
        impact_level=impact,
    )
    output_base._metadata = {"source": "file_analysis", "note": "Rails runner unavailable"}
    return output_base


def _find_method_line(file_path: Path, method_name: str) -> int:
    """ファイルからメソッド定義の行番号を返す。見つからない場合は 0。"""
    if not file_path.is_file():
        return 0
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    pattern = re.compile(rf"^\s*def\s+{re.escape(method_name)}\b")
    for i, line in enumerate(lines, start=1):
        if pattern.match(line):
            return i
    return 0


def _file_path_to_class_name(file_path: str) -> str:
    """app/models/user.rb → User, app/models/blog_post.rb → BlogPost"""
    import os
    basename = os.path.basename(file_path)
    stem = re.sub(r"\..+$", "", basename)
    return "".join(part.capitalize() for part in stem.split("_"))


async def _resolve_from_url(url: str, bridge: Any) -> str | None:
    """URLからコントローラ#アクションを解決する（ブリッジ経由）。"""
    try:
        data = await bridge.execute("dump_view_mapping.rb", args=["all"])
        mappings = data.get("mappings", [])
        normalized = url.rstrip("/")
        for m in mappings:
            path = m.get("path", "").rstrip("/")
            # Rails path pattern: /users/:id → regex
            pattern = re.sub(r":[^/]+", r"[^/]+", re.escape(path))
            if re.fullmatch(pattern, normalized):
                ctrl = m["controller"]  # "admin/users"
                action = m["action"]
                # controller → CamelCase
                parts = ctrl.split("/")
                camel_parts = ["".join(p.capitalize() for p in part.split("_")) for part in parts]
                ctrl_class = "::".join(camel_parts) + "Controller"
                return f"{ctrl_class}#{action}"
    except Exception:
        pass
    return None


# ============================================================
# full_inventory 実装
# ============================================================

# グループ名推定（日本語）
_GROUP_NAME_JA: dict[str, str] = {
    "admin": "管理画面",
    "api": "API",
    "users": "ユーザー管理",
    "orders": "注文管理",
    "products": "商品管理",
    "companies": "企業管理",
    "posts": "投稿管理",
    "comments": "コメント管理",
    "sessions": "セッション管理",
    "passwords": "パスワード管理",
    "registrations": "ユーザー登録",
    "confirmations": "確認メール管理",
    "dashboard": "ダッシュボード",
    "dashboards": "ダッシュボード",
    "settings": "設定",
    "profiles": "プロフィール管理",
    "notifications": "通知管理",
    "messages": "メッセージ管理",
    "payments": "支払い管理",
    "items": "アイテム管理",
    "categories": "カテゴリ管理",
    "tags": "タグ管理",
    "reports": "レポート",
    "searches": "検索",
}


def _resolve_group_name(namespace: str, resource: str, locale: str) -> str:
    """名前空間・リソースからグループ名を生成する。"""
    ns_lower = namespace.lower() if namespace else ""
    res_lower = resource.lower() if resource else ""

    if locale == "ja":
        if ns_lower == "admin":
            return "管理画面"
        if ns_lower in ("api", "api::v1", "api::v2"):
            # バージョン抽出
            ver_m = re.search(r"v(\d+)", ns_lower)
            ver = f" v{ver_m.group(1)}" if ver_m else ""
            return f"API{ver}"
        if ns_lower:
            return _GROUP_NAME_JA.get(ns_lower, f"{ns_lower.capitalize()} 管理")
        return _GROUP_NAME_JA.get(res_lower, f"{resource.replace('_', ' ').title()} 管理")
    else:
        if ns_lower == "admin":
            return "Admin"
        if ns_lower.startswith("api"):
            ver_m = re.search(r"v(\d+)", ns_lower)
            ver = f" v{ver_m.group(1)}" if ver_m else ""
            return f"API{ver}"
        if ns_lower:
            return ns_lower.replace("_", " ").title()
        return resource.replace("_", " ").title()


def _controller_to_namespace_resource(controller_action: str) -> tuple[str, str]:
    """コントローラ#アクションから (namespace, resource) を抽出する。

    例:
        "UsersController#index" → ("", "users")
        "Admin::UsersController#index" → ("Admin", "users")
        "Api::V1::UsersController#index" → ("Api::V1", "users")
    """
    resource, _action, namespaces = parse_controller_action(controller_action)
    namespace = "::".join(namespaces) if namespaces else ""
    return namespace, resource


def _group_screens(
    screens: list[ScreenEntry],
    group_by: str,
    locale: str,
) -> list[ScreenGroup]:
    """group_by に応じてスクリーンをグループ化する。"""
    if group_by == "flat":
        return [ScreenGroup(group_name="全画面" if locale == "ja" else "All", screens=screens)]

    if group_by == "resource":
        groups_map: dict[str, list[ScreenEntry]] = {}
        for screen in screens:
            _ns, resource = _controller_to_namespace_resource(screen.controller_action)
            key = resource
            groups_map.setdefault(key, []).append(screen)
        return [
            ScreenGroup(
                group_name=_resolve_group_name("", key, locale),
                screens=slist,
            )
            for key, slist in sorted(groups_map.items())
        ]

    # default: namespace
    groups_map_ns: dict[str, list[ScreenEntry]] = {}
    for screen in screens:
        ns, resource = _controller_to_namespace_resource(screen.controller_action)
        # APIはis_apiフラグで別扱いせず、名前空間でグループ
        key = ns if ns else resource
        groups_map_ns.setdefault(key, []).append(screen)

    return [
        ScreenGroup(
            group_name=_resolve_group_name(
                key if "::" not in key else key.split("::")[0], key, locale
            ),
            screens=slist,
        )
        for key, slist in sorted(groups_map_ns.items())
    ]


def _collect_shared_partials(
    partial_usage: dict[str, list[str]],
) -> list[SharedPartialEntry]:
    """パーシャル使用状況から SharedPartialEntry リストを生成する。

    partial_usage: {partial_file: [screen_controller_action, ...]}
    """
    result: list[SharedPartialEntry] = []
    for partial_file, screens in sorted(partial_usage.items()):
        count: int | str = len(screens)
        impact = _determine_impact_level(count)
        result.append(
            SharedPartialEntry(file=partial_file, screen_count=count, impact_level=impact)
        )
    # layout経由のパーシャルは "all (layout)"
    return result


def _build_screen_entry_from_mapping(
    mapping: dict[str, Any],
    config: Any,
    resolver: ViewResolver,
    parser: TemplateParser,
    name_resolver: ScreenNameResolver,
    locale: str,
) -> ScreenEntry | None:
    """mapping 辞書から ScreenEntry を構築する。"""
    ctrl = mapping.get("controller", "")
    action = mapping.get("action", "")
    if not ctrl or not action:
        return None

    url_pattern = mapping.get("path", f"/{ctrl}/{action}")
    http_method = mapping.get("verb", "GET")
    controller_action = _build_controller_action(ctrl, action)
    i18n_keys: dict[str, str] = mapping.get("i18n_title_keys", {}) or {}
    conventional_template = mapping.get("conventional_template", "")
    explicit_render = mapping.get("explicit_render")

    # is_api 判定
    project_root = Path(config.rails_project_path)
    is_api = bool(
        _is_api_route(mapping)
        or is_api_controller(ctrl, project_root)
        or is_json_only_action(ctrl, action, project_root)
    )

    # テンプレート解決
    template_rel: str | None = None
    if not is_api:
        if explicit_render and "/" in explicit_render:
            parts = explicit_render.split("/", 1)
            template_rel = resolver.find_template(parts[0], parts[1])
        if template_rel is None and conventional_template:
            parts = conventional_template.split("/", 1)
            if len(parts) == 2:
                template_rel = resolver.find_template(parts[0], parts[1])
        if template_rel is None:
            template_rel = resolver.find_template(ctrl, action)

    # 画面名推定
    screen_name, _source = name_resolver.resolve(
        controller_action,
        i18n_keys=i18n_keys,
        template_path=template_rel,
        locale=locale,
    )

    # パーシャル数
    partial_count = 0
    partial_files: list[str] = []
    if template_rel:
        with contextlib.suppress(Exception):
            nodes = resolver.resolve_partials(template_rel)
            partial_files = [n.file for n in nodes if n.file]
            partial_count = len(partial_files)

    # モデル参照
    models: list[str] = []
    if template_rel:
        with contextlib.suppress(Exception):
            analysis = parser.parse(template_rel)
            seen_models: set[str] = set()
            for ref in analysis.model_refs:
                model_name = _variable_to_model_name(ref.variable)
                if model_name not in seen_models:
                    seen_models.add(model_name)
                    models.append(model_name)

    # シリアライザ（APIの場合）
    serializer: str | None = None
    if is_api:
        with contextlib.suppress(Exception):
            serializer = detect_serializer(ctrl, action, project_root)

    return ScreenEntry(
        screen_name=screen_name,
        url_pattern=url_pattern,
        http_method=http_method,
        controller_action=controller_action,
        template=template_rel,
        partial_count=partial_count,
        models=models,
        is_api=is_api,
        serializer=serializer,
    )


def _fallback_full_inventory(
    config: Any, locale: str, group_by: str, include_api: bool
) -> FullInventoryOutput:
    """bridge 失敗時のファイルベースフォールバック。

    app/views 配下の非パーシャルビューをスキャンして ScreenEntry を構築する。
    """
    project_root = Path(config.rails_project_path)
    views_dir = project_root / "app" / "views"
    generated_at = datetime.now(tz=UTC).isoformat()

    resolver = ViewResolver(config)
    parser = TemplateParser(config)
    name_resolver = ScreenNameResolver(config)

    screens: list[ScreenEntry] = []
    partial_usage: dict[str, list[str]] = {}

    if views_dir.is_dir():
        for tpl in sorted(views_dir.rglob("*")):
            if not tpl.is_file():
                continue
            if tpl.suffix not in {".erb", ".haml", ".slim"}:
                continue
            if tpl.name.startswith("_"):
                continue  # パーシャルはスキップ

            try:
                rel = str(tpl.relative_to(project_root))
            except ValueError:
                rel = str(tpl)

            # レイアウトはスキップ
            if "views/layouts" in rel.replace("\\", "/"):
                continue

            # controller_action を推定
            parts = rel.replace("\\", "/").split("/")
            if len(parts) < 4 or parts[0] != "app" or parts[1] != "views":
                continue

            # action: ファイル名から拡張子除去
            filename = parts[-1]
            action = re.sub(r"\..+$", "", filename)
            # コントローラパス
            ctrl_parts = parts[2:-1]
            ctrl = "/".join(ctrl_parts)
            controller_action = _build_controller_action(ctrl, action)

            # HTTP method guess from action name
            method_map = {
                "index": "GET", "show": "GET", "new": "GET", "edit": "GET",
                "create": "POST", "update": "PATCH", "destroy": "DELETE",
            }
            http_method = method_map.get(action, "GET")
            url_parts = [p.replace("_", "-") for p in ctrl_parts]
            url_pattern = "/" + "/".join(url_parts)

            screen_name, _ = name_resolver.resolve(
                controller_action, template_path=rel, locale=locale
            )

            # パーシャル解決
            partial_count = 0
            models: list[str] = []
            with contextlib.suppress(Exception):
                nodes = resolver.resolve_partials(rel)
                partial_count = len([n for n in nodes if n.file])
                for node in nodes:
                    if node.file:
                        partial_usage.setdefault(node.file, []).append(controller_action)

            with contextlib.suppress(Exception):
                analysis = parser.parse(rel)
                seen: set[str] = set()
                for ref in analysis.model_refs:
                    m = _variable_to_model_name(ref.variable)
                    if m not in seen:
                        seen.add(m)
                        models.append(m)

            entry = ScreenEntry(
                screen_name=screen_name,
                url_pattern=url_pattern,
                http_method=http_method,
                controller_action=controller_action,
                template=rel,
                partial_count=partial_count,
                models=models,
                is_api=False,
            )
            screens.append(entry)

    if not include_api:
        screens = [s for s in screens if not s.is_api]

    groups = _group_screens(screens, group_by, locale)
    web_count = sum(1 for s in screens if not s.is_api)
    api_count = sum(1 for s in screens if s.is_api)

    # 共有パーシャル（2画面以上で使用）
    shared_partials = _collect_shared_partials(
        {k: v for k, v in partial_usage.items() if len(v) >= 2}
    )

    output = FullInventoryOutput(
        generated_at=generated_at,
        total_screen_count=len(screens),
        web_screen_count=web_count,
        api_endpoint_count=api_count,
        groups=groups,
        shared_partials=shared_partials,
        markdown=None,
    )
    output._metadata = {"source": "file_analysis", "note": "Rails runner unavailable"}
    return output


async def _full_inventory_impl(
    params: ScreenMapInput,
    bridge: Any,
    config: Any,
    ctx: Context[Any, Any, Any] | None = None,
) -> FullInventoryOutput:
    """full_inventory モードのメイン処理。"""
    locale = params.locale or "ja"
    group_by = (params.group_by or ScreenMapGroupBy.NAMESPACE).value
    include_api = params.include_api if params.include_api is not None else True
    generated_at = datetime.now(tz=UTC).isoformat()

    resolver = ViewResolver(config)
    parser = TemplateParser(config)
    name_resolver = ScreenNameResolver(config)

    # bridge から全ルーティング取得
    mappings: list[dict[str, Any]] = []
    is_fallback = False
    try:
        data = await bridge.execute("dump_view_mapping.rb", args=["all"])
        mappings = data.get("mappings", [])
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        is_fallback = True

    if is_fallback or not mappings:
        output = _fallback_full_inventory(config, locale, group_by, include_api)
        if params.format == "markdown":
            formatter = InventoryFormatter()
            output.markdown = formatter.format(output)
        return output

    # ---- bridge 成功: ルーティングを処理 ----
    total = len(mappings)
    screens: list[ScreenEntry] = []
    partial_usage: dict[str, list[str]] = {}

    for i, mapping in enumerate(mappings):
        if ctx is not None:
            await ctx.report_progress(i, total, f"画面を解析中: {i + 1}/{total}")

        entry = _build_screen_entry_from_mapping(
            mapping, config, resolver, parser, name_resolver, locale
        )
        if entry is None:
            continue

        # パーシャル使用状況集計
        if entry.template:
            with contextlib.suppress(Exception):
                nodes = resolver.resolve_partials(entry.template)
                for node in nodes:
                    if node.file:
                        partial_usage.setdefault(node.file, []).append(entry.controller_action)

        screens.append(entry)

    if ctx is not None:
        await ctx.report_progress(total, total, "グルーピング・整形中...")

    if not include_api:
        screens = [s for s in screens if not s.is_api]

    groups = _group_screens(screens, group_by, locale)
    web_count = sum(1 for s in screens if not s.is_api)
    api_count = sum(1 for s in screens if s.is_api)

    # 共有パーシャル（2画面以上）
    shared_partials_raw = {k: v for k, v in partial_usage.items() if len(v) >= 2}
    shared_partials = _collect_shared_partials(shared_partials_raw)

    output = FullInventoryOutput(
        generated_at=generated_at,
        total_screen_count=len(screens),
        web_screen_count=web_count,
        api_endpoint_count=api_count,
        groups=groups,
        shared_partials=shared_partials,
        markdown=None,
    )

    if params.format == "markdown":
        formatter = InventoryFormatter()
        output.markdown = formatter.format(output)

    return output


# ============================================================
# MCP ツール登録
# ============================================================


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_screen_map",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def screen_map(params: ScreenMapInput, ctx: Context[Any, Any, Any]) -> str:
        """画面とソースコードの双方向マッピングを提供する。

        3つのモードがある:
        - screen_to_source: URL またはコントローラ名から、その画面を構成する全ファイルを返す
        - source_to_screens: ファイルパスから、そのファイルが使われている全画面を返す
        - full_inventory: 全画面の台帳を自動生成する
          （ドキュメントがないプロジェクトの全体把握に有効）

        画面を変更する前にこのツールで影響範囲を確認すること。
        特にパーシャルやヘルパーの変更は複数画面に影響する可能性がある。
        """
        try:
            config, bridge, _cache, _grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        if params.mode == ScreenMapMode.SCREEN_TO_SOURCE:
            if not params.url and not params.controller_action:
                return ErrorResponse(
                    code="INVALID_INPUT",
                    message="screen_to_source モードでは url または controller_action が必要です",
                ).model_dump_json(indent=2)
            try:
                output = await _screen_to_source_impl(params, bridge, config)
                return output.model_dump_json(indent=2, exclude={"_metadata"})
            except ValueError as e:
                return ErrorResponse(
                    code="INVALID_INPUT", message=str(e)
                ).model_dump_json(indent=2)
            except Exception as e:
                return ErrorResponse(
                    code="RUNTIME_ERROR", message=str(e)
                ).model_dump_json(indent=2)

        if params.mode == ScreenMapMode.SOURCE_TO_SCREENS:
            if not params.file_path:
                return ErrorResponse(
                    code="INVALID_INPUT",
                    message="source_to_screens モードでは file_path が必要です",
                ).model_dump_json(indent=2)
            try:
                s2s_output = await _source_to_screens_impl(params, bridge, config)
                return s2s_output.model_dump_json(indent=2, exclude={"_metadata"})
            except ValueError as e:
                return ErrorResponse(
                    code="INVALID_INPUT", message=str(e)
                ).model_dump_json(indent=2)
            except Exception as e:
                return ErrorResponse(
                    code="RUNTIME_ERROR", message=str(e)
                ).model_dump_json(indent=2)

        if params.mode == ScreenMapMode.FULL_INVENTORY:
            try:
                inv_output = await _full_inventory_impl(params, bridge, config, ctx)
                return inv_output.model_dump_json(indent=2, exclude={"_metadata"})
            except ValueError as e:
                return ErrorResponse(
                    code="INVALID_INPUT", message=str(e)
                ).model_dump_json(indent=2)
            except Exception as e:
                return ErrorResponse(
                    code="RUNTIME_ERROR", message=str(e)
                ).model_dump_json(indent=2)

        return ErrorResponse(
            code="NOT_IMPLEMENTED",
            message=f"モード '{params.mode.value}' は未対応です",
        ).model_dump_json(indent=2)

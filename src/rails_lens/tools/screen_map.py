"""rails_lens_screen_map ツール（Phase H-2: screen_to_source モード）"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from rails_lens.analyzers.screen_name_resolver import ScreenNameResolver, parse_controller_action
from rails_lens.analyzers.template_parser import TemplateParser
from rails_lens.analyzers.view_resolver import PartialNode, ViewResolver
from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse

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
        raise ValueError("url または controller_action が必要です")

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
    async def screen_map(params: ScreenMapInput) -> str:
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

        # source_to_screens / full_inventory は Phase H-3 以降で実装予定
        return ErrorResponse(
            code="NOT_IMPLEMENTED",
            message=f"モード '{params.mode.value}' は現在実装中です",
        ).model_dump_json(indent=2)

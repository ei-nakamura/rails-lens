"""画面名推定ロジック（優先順位: i18n > content_for/title/h1 > RESTful規約）"""
from __future__ import annotations

import re
from pathlib import Path

from rails_lens.config import RailsLensConfig

# ============================================================
# RESTful規約マッピング
# ============================================================

_RESTFUL_JA: dict[str, str] = {
    "index": "{resource}一覧",
    "show": "{resource}詳細",
    "new": "{resource}新規作成",
    "edit": "{resource}編集",
    "create": "{resource}作成（処理）",
    "update": "{resource}更新（処理）",
    "destroy": "{resource}削除（処理）",
}

_RESTFUL_EN: dict[str, str] = {
    "index": "{Resource} List",
    "show": "{Resource} Detail",
    "new": "New {Resource}",
    "edit": "Edit {Resource}",
    "create": "Create {Resource} (action)",
    "update": "Update {Resource} (action)",
    "destroy": "Delete {Resource} (action)",
}

# i18nキー探索パターン（優先順位順）
_I18N_KEY_PATTERNS = [
    "{resource}.{action}.title",
    "{resource}.{action}.page_title",
    "titles.{resource}.{action}",
    "views.{resource}.{action}.title",
]

# テンプレートタイトル抽出パターン
_CONTENT_FOR_TITLE = re.compile(
    r"""content_for\s+:title[,\s]+['"]([^'"]+)['"]""", re.MULTILINE
)
_PROVIDE_TITLE = re.compile(
    r"""provide\s+:title\s*,\s*['"]([^'"]+)['"]""", re.MULTILINE
)
_TITLE_TAG = re.compile(r"""<title>([^<]+)</title>""")
_H1_TAG = re.compile(r"""<h1[^>]*>([^<]+)</h1>""")

# CamelCase → snake_case
_CAMEL_RE = re.compile(r"([A-Z])")


def _to_snake_case(name: str) -> str:
    return _CAMEL_RE.sub(lambda m: "_" + m.group(1).lower(), name).lstrip("_")


def _capitalize_words(snake: str) -> str:
    """snake_case → 'Title Words'"""
    return " ".join(w.capitalize() for w in snake.split("_"))


def parse_controller_action(controller_action: str) -> tuple[str, str, list[str]]:
    """コントローラ#アクション文字列を分解する。

    Args:
        controller_action: "UsersController#show" / "Admin::UsersController#show" / "users#show"

    Returns:
        (resource_snake, action, namespaces) のタプル
        例: ("user", "show", ["Admin"])
    """
    parts = controller_action.split("#", 1)
    if len(parts) != 2:
        return "unknown", "index", []

    ctrl_part, action = parts[0].strip(), parts[1].strip()

    # "::" で名前空間を分解
    components = ctrl_part.split("::")
    ctrl_name = components[-1]
    namespaces = components[:-1]

    # CamelCase → snake_case, Controller サフィックスを除去
    ctrl_no_suffix = re.sub(r"Controller$", "", ctrl_name)
    if re.search(r"[A-Z]", ctrl_no_suffix):
        # CamelCase style: UsersController → users
        resource = _to_snake_case(ctrl_no_suffix)
    else:
        # already snake_case: "admin/users" → "users"
        resource = ctrl_no_suffix.split("/")[-1]

    return resource, action, namespaces


def _namespace_prefix_suffix(
    namespaces: list[str], locale: str
) -> tuple[str, str]:
    """名前空間からプレフィックスとサフィックスを生成する。

    Returns:
        (prefix, suffix) のタプル
    """
    if not namespaces:
        return "", ""

    ns_str = "::".join(namespaces)
    is_admin = any(n.lower() == "admin" for n in namespaces)
    is_api = any(n.lower() in ("api",) for n in namespaces)

    if is_admin:
        if locale == "ja":
            return "管理画面 - ", ""
        else:
            return "Admin - ", ""

    if is_api:
        ver_m = re.search(r"[Vv](\d+)", ns_str)
        ver = f" v{ver_m.group(1)}" if ver_m else ""
        if locale == "ja":
            return "", f" (API{ver})"
        else:
            return "", f" (API{ver})"

    # Generic namespace
    if locale == "ja":
        return f"{ns_str} - ", ""
    else:
        return f"{ns_str} - ", ""


class ScreenNameResolver:
    """画面名推定ロジック。優先順位に従って画面名を推定する。"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self.project_root = Path(config.rails_project_path)

    def resolve(
        self,
        controller_action: str,
        i18n_keys: dict[str, str] | None = None,
        template_path: str | None = None,
        locale: str = "ja",
    ) -> tuple[str, str]:
        """画面名を推定する。

        Args:
            controller_action: "UsersController#show" 等
            i18n_keys: dump_view_mapping から取得したi18nキー辞書 {key: value}
            template_path: テンプレートファイルのパス（相対 or 絶対）
            locale: 言語設定 ("ja" | "en")

        Returns:
            (screen_name, screen_name_source) のタプル
            screen_name_source: "i18n:{key}" | "content_for_title" | "h1_tag" | "restful_convention"
        """
        resource, action, namespaces = parse_controller_action(controller_action)
        prefix, suffix = _namespace_prefix_suffix(namespaces, locale)

        # Priority 1: i18n keys
        if i18n_keys:
            for pattern in _I18N_KEY_PATTERNS:
                key = pattern.format(resource=resource, action=action)
                if key in i18n_keys and i18n_keys[key]:
                    name = i18n_keys[key]
                    return f"{prefix}{name}{suffix}", f"i18n:{key}"

        # Priority 2 & 3: テンプレートからタイトル抽出
        if template_path:
            name, source = self._extract_from_template(template_path)
            if name:
                return f"{prefix}{name}{suffix}", source

        # Priority 4: RESTful規約
        name = self._restful_name(resource, action, locale)
        return f"{prefix}{name}{suffix}", "restful_convention"

    def _extract_from_template(self, template_path: str) -> tuple[str, str]:
        """テンプレートからタイトル情報を抽出する。"""
        p = Path(template_path)
        abs_path = p if p.is_absolute() else self.project_root / p
        if not abs_path.exists():
            return "", ""

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", ""

        # Priority 2a: content_for :title
        m = _CONTENT_FOR_TITLE.search(source)
        if m:
            return m.group(1).strip(), "content_for_title"

        # Priority 2b: provide :title
        m = _PROVIDE_TITLE.search(source)
        if m:
            return m.group(1).strip(), "content_for_title"

        # Priority 2c: <title> タグ
        m = _TITLE_TAG.search(source)
        if m:
            return m.group(1).strip(), "content_for_title"

        # Priority 3: <h1> タグ
        m = _H1_TAG.search(source)
        if m:
            return m.group(1).strip(), "h1_tag"

        return "", ""

    def _restful_name(self, resource: str, action: str, locale: str) -> str:
        """RESTful規約から画面名を生成する。"""
        resource_display = _capitalize_words(resource)
        if locale == "ja":
            template = _RESTFUL_JA.get(action, resource_display)
            return template.replace("{resource}", resource_display)
        else:
            template = _RESTFUL_EN.get(action, resource_display)
            return template.replace("{Resource}", resource_display)

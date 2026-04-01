"""APIエンドポイントとシリアライザの静的解析による検出"""
from __future__ import annotations

import re
from pathlib import Path

# ---- 検出パターン ----
_API_BASE_INHERIT = re.compile(r"ActionController::API")
_RESPOND_TO_JSON_ONLY_INLINE = re.compile(r"respond_to\s*:json\b")
_RESPOND_TO_HTML = re.compile(r"respond_to\s*:html\b|format\.html\b")


# ============================================================
# コントローラファイル解決
# ============================================================


def _find_controller_file(controller: str, project_root: Path) -> Path | None:
    """controller (e.g. "users", "admin/users") → コントローラファイルを探す。"""
    ctrl_dir = project_root / "app" / "controllers"
    # "admin/users" → app/controllers/admin/users_controller.rb
    parts = controller.split("/")
    filename = parts[-1] + "_controller.rb"
    subdir = ctrl_dir / Path(*parts[:-1]) if len(parts) > 1 else ctrl_dir
    candidate = subdir / filename
    if candidate.is_file():
        return candidate
    return None


# ============================================================
# API コントローラ判定
# ============================================================


def is_api_controller(controller: str, project_root: Path) -> bool:
    """コントローラが API 専用かどうかを判定する。

    以下のいずれかに該当する場合 True:
    - コントローラのパスが "api/" で始まる（名前空間）
    - コントローラファイルが ActionController::API を継承している
    """
    ctrl_lower = controller.lower()
    if ctrl_lower.startswith("api/") or "/api/" in ctrl_lower:
        return True

    ctrl_file = _find_controller_file(controller, project_root)
    if ctrl_file is not None:
        try:
            content = ctrl_file.read_text(encoding="utf-8", errors="replace")
            if _API_BASE_INHERIT.search(content):
                return True
        except OSError:
            pass

    return False


def is_json_only_action(controller: str, action: str, project_root: Path) -> bool:
    """アクションが JSON レスポンスのみかどうかを判定する。

    以下のパターンを検出:
    - respond_to :json のみ（:html がない）
    - respond_to ブロック内で format.json のみ（format.html がない）
    """
    ctrl_file = _find_controller_file(controller, project_root)
    if ctrl_file is None:
        return False

    try:
        content = ctrl_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    # アクションメソッドのブロックを抽出
    action_pattern = re.compile(
        rf"def\s+{re.escape(action)}\b(.*?)(?=\n\s*def\s|\Z)",
        re.DOTALL,
    )
    m = action_pattern.search(content)
    if not m:
        return False

    action_body = m.group(1)

    # respond_to :json のみ（インライン形式）
    json_only = _RESPOND_TO_JSON_ONLY_INLINE.search(action_body)
    if json_only and not _RESPOND_TO_HTML.search(action_body):
        return True

    # respond_to ブロック形式
    respond_to_m = re.search(
        r"respond_to\s+do\s*\|format\|(.*?)end",
        action_body,
        re.DOTALL,
    )
    if respond_to_m:
        block = respond_to_m.group(1)
        if "format.json" in block and "format.html" not in block:
            return True

    return False


# ============================================================
# シリアライザ検出
# ============================================================


def detect_serializer(
    controller: str, action: str, project_root: Path
) -> str | None:
    """シリアライザを検出する。

    検出順序:
    1. jbuilder: app/views/{controller}/{action}.json.jbuilder
    2. ActiveModelSerializers: app/serializers/ 内の *Serializer クラス
    3. Blueprinter: app/blueprints/ 内のクラス
    4. JSONAPI::Serializer: app/serializers/ 内で JSONAPI::Serializer を include

    Returns:
        シリアライザクラス名または相対ファイルパス、未検出の場合 None
    """
    resource = controller.split("/")[-1]
    resource_singular = _singularize(resource)
    resource_camel = "".join(p.capitalize() for p in resource_singular.split("_"))

    # 1. jbuilder
    views_dir = project_root / "app" / "views"
    for subdir in [controller, resource]:
        jbuilder = views_dir / subdir / f"{action}.json.jbuilder"
        if jbuilder.is_file():
            try:
                return str(jbuilder.relative_to(project_root))
            except ValueError:
                return str(jbuilder)

    # 2. ActiveModelSerializers（JSONAPI 以外）
    serializers_dir = project_root / "app" / "serializers"
    if serializers_dir.is_dir():
        # 候補クラス名パターン
        candidates = {
            f"{resource_camel}Serializer",
            f"{resource_singular.capitalize()}Serializer",
        }
        for rb in sorted(serializers_dir.rglob("*.rb")):
            try:
                content = rb.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "JSONAPI::Serializer" in content:
                continue
            class_m = re.search(r"class\s+(\w+)", content)
            if class_m and class_m.group(1) in candidates:
                return class_m.group(1)

    # 3. Blueprinter
    blueprints_dir = project_root / "app" / "blueprints"
    if blueprints_dir.is_dir():
        for rb in sorted(blueprints_dir.rglob("*.rb")):
            try:
                content = rb.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            is_blueprint = "Blueprinter::Base" in content or "< Blueprinter" in content
            name_match = (
                resource_singular.lower() in rb.stem.lower()
                or resource.lower() in rb.stem.lower()
            )
            if is_blueprint and name_match:
                class_m = re.search(r"class\s+(\w+)", content)
                return class_m.group(1) if class_m else rb.stem

    # 4. JSONAPI::Serializer
    if serializers_dir.is_dir():
        for rb in sorted(serializers_dir.rglob("*.rb")):
            try:
                content = rb.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "JSONAPI::Serializer" not in content:
                continue
            if resource_singular.lower() in rb.stem.lower() or resource.lower() in rb.stem.lower():
                class_m = re.search(r"class\s+(\w+)", content)
                return class_m.group(1) if class_m else rb.stem

    return None


# ============================================================
# ユーティリティ
# ============================================================


def _singularize(word: str) -> str:
    """シンプルな英単語の単数化（ヒューリスティック）。"""
    if word.endswith("ies"):
        return word[:-3] + "y"
    if any(word.endswith(s) for s in ("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word

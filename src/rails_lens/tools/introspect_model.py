"""rails_lens_introspect_model ツール"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from difflib import get_close_matches
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError, RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse, IntrospectModelInput

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    """MCPサーバーにツールを登録する"""

    @mcp.tool(
        name="rails_lens_introspect_model",
        annotations=ToolAnnotations(
            title="Introspect Rails Model",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def introspect_model(params: IntrospectModelInput) -> str:
        """モデルの全依存関係（associations, callbacks, validations,
        scopes, concerns, schema等）を返す。
        モデルを変更する前に必ずこのツールで影響範囲を確認すること。
        """
        try:
            config, bridge, cache, _ = get_deps()
        except Exception as e:
            return _error_json("INITIALIZATION_ERROR", str(e))

        model_name = params.model_name

        # 1. キャッシュ確認
        cache_key = model_name
        cached = cache.get("introspect_model", cache_key)
        if cached is not None:
            return _filter_sections(cached, params.sections)

        # 2. rails runner 実行
        try:
            raw_data = await bridge.execute(
                "introspect_model.rb",
                args=[model_name],
            )
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raw_data = _fallback_file_analysis(config, params)
            raw_data["_metadata"] = {"source": "file_analysis", "note": "Rails runner unavailable"}
        except RailsLensError as e:
            # ModelNotFoundError の場合、サジェストを追加
            suggestion = getattr(e, "suggestion", None)
            if suggestion is None and hasattr(e, "__context__"):
                pass
            # all_models が error details に含まれている場合はサジェスト
            return _error_json(e.code, str(e), suggestion=suggestion)

        # 3. キャッシュ保存
        source_files = _extract_source_files(raw_data, model_name)
        cache.set("introspect_model", cache_key, raw_data, source_files)

        # 4. セクションフィルタリング & 返却
        return _filter_sections(raw_data, params.sections)


async def introspect_model_impl(
    params: IntrospectModelInput,
    bridge: Any,
    cache: Any,
) -> dict[str, Any]:
    """MCPデコレータなしで同じロジックを実行し、dict を返す"""
    model_name = params.model_name

    cached = cache.get("introspect_model", model_name)
    if cached is not None:
        if params.sections is None:
            return cast(dict[str, Any], cached)
        return {
            k: v for k, v in cached.items()
            if k in params.sections or k in ("model_name", "table_name", "file_path")
        }

    raw_data = await bridge.execute("introspect_model.rb", args=[model_name])
    source_files = _extract_source_files(raw_data, model_name)
    cache.set("introspect_model", model_name, raw_data, source_files)

    if params.sections is None:
        return cast(dict[str, Any], raw_data)
    return {
        k: v for k, v in raw_data.items()
        if k in params.sections or k in ("model_name", "table_name", "file_path")
    }


def _filter_sections(data: dict[str, Any], sections: list[str] | None) -> str:
    """指定されたセクションのみを含むJSONを返す"""
    if sections is None:
        return json.dumps(data, ensure_ascii=False, indent=2)

    filtered = {
        k: v for k, v in data.items()
        if k in sections or k in ("model_name", "table_name", "file_path")
    }
    return json.dumps(filtered, ensure_ascii=False, indent=2)


def _extract_source_files(data: dict[str, Any], model_name: str) -> list[str]:
    """キャッシュ無効化に使うソースファイルのリストを抽出する"""
    files = ["db/schema.rb"]

    file_path = data.get("file_path")
    if file_path:
        files.append(file_path)

    for concern in data.get("concerns", []):
        sf = concern.get("source_file")
        if sf:
            files.append(sf)

    return files


def _error_json(code: str, message: str, suggestion: str | None = None) -> str:
    """エラーレスポンスをJSON文字列として返す"""
    resp = ErrorResponse(code=code, message=message, suggestion=suggestion)
    return resp.model_dump_json(indent=2)


def _suggest_similar_models(model_name: str, all_models: list[str]) -> list[str]:
    """類似モデル名のサジェストを返す"""
    return get_close_matches(model_name, all_models, n=3, cutoff=0.4)


def _model_name_to_path(model_name: str) -> str:
    """'Admin::Company' -> 'admin/company.rb'"""
    parts = model_name.split("::")
    underscored = []
    for part in parts:
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", part)
        s2 = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1)
        underscored.append(s2.lower())
    return "/".join(underscored) + ".rb"


def _fallback_file_analysis(config: Any, params: IntrospectModelInput) -> dict[str, Any]:
    """Rails runner が使えない場合にモデルファイルから静的解析する"""
    model_name = params.model_name
    rel_path = _model_name_to_path(model_name)
    model_file = Path(config.rails_project_path) / "app" / "models" / rel_path

    result: dict[str, Any] = {
        "model_name": model_name,
        "table_name": "",
        "file_path": str(model_file),
        "associations": [],
        "callbacks": [],
        "validations": [],
        "scopes": [],
    }

    if not model_file.exists():
        return result

    content = model_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    assoc_re = re.compile(
        r"^\s*(has_many|belongs_to|has_one|has_and_belongs_to_many)\s+:(\w+)(.*)"
    )
    callback_re = re.compile(
        r"^\s*(before|after|around)_(save|create|update|destroy|validation|commit|initialize|find|touch)\s+:(\w+)(.*)"
    )
    validation_re = re.compile(r"^\s*(validates|validate)\s+:(\w+)(.*)")
    scope_re = re.compile(r"^\s*scope\s+:(\w+)")

    for lineno, line in enumerate(lines, start=1):
        m = assoc_re.match(line)
        if m:
            result["associations"].append({
                "type": m.group(1),
                "name": m.group(2),
                "class_name": m.group(2).capitalize(),
                "source_file": str(model_file),
                "source_line": lineno,
            })
            continue

        m = callback_re.match(line)
        if m:
            result["callbacks"].append({
                "kind": m.group(1),
                "event": m.group(2),
                "method_name": m.group(3),
                "source_file": str(model_file),
                "source_line": lineno,
                "conditions": {},
            })
            continue

        m = validation_re.match(line)
        if m:
            result["validations"].append({
                "type": m.group(1),
                "attributes": [m.group(2)],
                "source_file": str(model_file),
                "source_line": lineno,
                "options": {},
            })
            continue

        m = scope_re.match(line)
        if m:
            result["scopes"].append({
                "name": m.group(1),
                "source_file": str(model_file),
                "source_line": lineno,
            })

    return result

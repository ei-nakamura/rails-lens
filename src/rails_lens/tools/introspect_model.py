"""rails_lens_introspect_model ツール"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from difflib import get_close_matches
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError
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

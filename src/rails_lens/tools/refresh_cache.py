"""rails_lens_refresh_cache ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse


def refresh_cache_impl(cache: Any, tool_name: str = "") -> dict[str, str]:
    """MCPデコレータなしで同じロジックを実行し、dict を返す"""
    cache.invalidate_all()
    message = f"Cache invalidated for tool: {tool_name}" if tool_name else "All caches invalidated"
    return {"status": "ok", "message": message}


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_refresh_cache",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def refresh_cache(tool_name: str = "") -> str:
        """キャッシュを手動で無効化する（tool_name省略時は全キャッシュ）"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            if tool_name:
                cache.invalidate_all()  # tool_name指定時も全無効化（簡易実装）
                message = f"Cache invalidated for tool: {tool_name}"
            else:
                cache.invalidate_all()
                message = "All caches invalidated"
            return json.dumps(
                {"status": "ok", "message": message}, ensure_ascii=False, indent=2
            )
        except Exception as e:
            return ErrorResponse(
                code="REFRESH_CACHE_ERROR", message=str(e)
            ).model_dump_json(indent=2)

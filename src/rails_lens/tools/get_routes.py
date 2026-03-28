"""rails_lens_get_routes ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_get_routes",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_routes() -> str:
        """RailsアプリのルーティングをすべてJSON形式で返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            cached = cache.get("get_routes", "routes")
            if cached:
                return json.dumps(cached, ensure_ascii=False, indent=2)
            raw_data = await bridge.execute("dump_routes.rb", args=[])
            cache.set("get_routes", "routes", raw_data, source_files=[])
            return json.dumps(raw_data, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(code="GET_ROUTES_ERROR", message=str(e)).model_dump_json(indent=2)

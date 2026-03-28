"""rails_lens_gem_introspect ツール"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse, GemIntrospectInput, GemIntrospectOutput


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_gem_introspect",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def gem_introspect(params: GemIntrospectInput) -> str:
        """モデルに影響を与えているGemのメソッド・コールバック・ルートを返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            raw_data = await bridge.execute(
                "gem_introspect.rb",
                args=[params.model_name, params.gem_name or ""],
            )
            output = GemIntrospectOutput(**raw_data)
            return output.model_dump_json(indent=2)
        except Exception as e:
            return ErrorResponse(
                code="GEM_INTROSPECT_ERROR", message=str(e)
            ).model_dump_json(indent=2)

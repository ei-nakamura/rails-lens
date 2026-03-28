"""rails_lens_explain_method_resolution ツール"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse, MethodResolutionInput, MethodResolutionOutput


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_explain_method_resolution",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def explain_method_resolution(params: MethodResolutionInput) -> str:
        """モデルのメソッド解決順序（MRO）・祖先チェーン・メソッドオーナーを返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            raw_data = await bridge.execute(
                "method_resolution.rb",
                args=[
                    params.model_name,
                    params.method_name or "",
                    str(params.show_internal).lower(),
                ],
            )
            output = MethodResolutionOutput(**raw_data)
            return output.model_dump_json(indent=2)
        except Exception as e:
            return ErrorResponse(
                code="METHOD_RESOLUTION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

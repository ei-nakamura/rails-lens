"""rails_lens_trace_callback_chain ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import TraceCallbackChainInput, TraceCallbackChainOutput


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_trace_callback_chain",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def trace_callback_chain(params: TraceCallbackChainInput) -> str:
        """コールバック連鎖トレース"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            from rails_lens.models import ErrorResponse
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        raw_data = await bridge.execute(
            "trace_callbacks.rb", args=[params.model_name, params.lifecycle_event]
        )
        cache.set(
            "trace_callback_chain",
            f"{params.model_name}__{params.lifecycle_event}",
            raw_data,
        )
        output = TraceCallbackChainOutput(
            model_name=raw_data.get("model_name", params.model_name),
            lifecycle_event=raw_data.get("lifecycle_event", params.lifecycle_event),
            execution_order=raw_data.get("execution_order", []),
        )
        return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)

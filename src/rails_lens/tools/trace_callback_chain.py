"""rails_lens_trace_callback_chain ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError
from rails_lens.models import ErrorResponse, TraceCallbackChainInput, TraceCallbackChainOutput


async def trace_callback_chain_impl(
    params: TraceCallbackChainInput,
    bridge: Any,
    cache: Any,
) -> TraceCallbackChainOutput:
    """MCPデコレータなしで同じロジックを実行し、TraceCallbackChainOutput を返す"""
    raw_data = await bridge.execute(
        "trace_callbacks.rb", args=[params.model_name, params.lifecycle_event]
    )
    cache.set(
        "trace_callback_chain",
        f"{params.model_name}__{params.lifecycle_event}",
        raw_data,
    )
    execution_order = raw_data.get("execution_order", [])
    mermaid = _generate_mermaid_diagram(
        model_name=raw_data.get("model_name", params.model_name),
        lifecycle_event=raw_data.get("lifecycle_event", params.lifecycle_event),
        callbacks=execution_order,
    )
    return TraceCallbackChainOutput(
        model_name=raw_data.get("model_name", params.model_name),
        lifecycle_event=raw_data.get("lifecycle_event", params.lifecycle_event),
        execution_order=execution_order,
        mermaid_diagram=mermaid,
    )


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
            return _error_json("INITIALIZATION_ERROR", str(e))

        try:
            raw_data = await bridge.execute(
                "trace_callbacks.rb", args=[params.model_name, params.lifecycle_event]
            )
        except RailsLensError as e:
            return _error_json(e.code, str(e))

        cache.set(
            "trace_callback_chain",
            f"{params.model_name}__{params.lifecycle_event}",
            raw_data,
        )

        execution_order = raw_data.get("execution_order", [])
        mermaid = _generate_mermaid_diagram(
            model_name=raw_data.get("model_name", params.model_name),
            lifecycle_event=raw_data.get("lifecycle_event", params.lifecycle_event),
            callbacks=execution_order,
        )

        output = TraceCallbackChainOutput(
            model_name=raw_data.get("model_name", params.model_name),
            lifecycle_event=raw_data.get("lifecycle_event", params.lifecycle_event),
            execution_order=execution_order,
            mermaid_diagram=mermaid,
        )
        return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)


def _generate_mermaid_diagram(
    model_name: str,
    lifecycle_event: str,
    callbacks: list[dict[str, Any]],
) -> str:
    """コールバック連鎖のMermaid sequence diagramを生成する"""
    participants: dict[str, str] = {"App": "Application"}
    participants[model_name] = f"{model_name} Model"

    for cb in callbacks:
        concern = cb.get("defined_in_concern")
        if concern and concern not in participants:
            participants[concern] = f"{concern} Concern"

    lines = ["sequenceDiagram"]
    for pid, label in participants.items():
        lines.append(f"    participant {pid} as {label}")

    lines.append(f"    App->>{model_name}: {lifecycle_event}")

    before_done = False
    for cb in callbacks:
        kind = cb["kind"]
        method = cb["method_name"]
        source = cb.get("defined_in_concern") or model_name
        condition = ""

        conds = cb.get("conditions", {})
        if conds.get("if"):
            condition = f" (if: {conds['if']})"
        elif conds.get("unless"):
            condition = f" (unless: {conds['unless']})"

        if not before_done and kind in ("after",):
            lines.append(f"    Note over {model_name}: --- DB Write ---")
            before_done = True

        lines.append(f"    {model_name}->>{source}: {kind}_{lifecycle_event} :{method}{condition}")

    return "\n".join(lines)


def _error_json(code: str, message: str, suggestion: str | None = None) -> str:
    """エラーレスポンスをJSON文字列として返す"""
    resp = ErrorResponse(code=code, message=message, suggestion=suggestion)
    return resp.model_dump_json(indent=2)

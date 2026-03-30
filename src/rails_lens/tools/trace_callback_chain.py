"""rails_lens_trace_callback_chain ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError, RailsRunnerExecutionError, RailsRunnerTimeoutError
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

        fallback_metadata = None
        try:
            raw_data = await bridge.execute(
                "trace_callbacks.rb", args=[params.model_name, params.lifecycle_event]
            )
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raw_data = _fallback_trace_callbacks(config, params)
            fallback_metadata = {"source": "file_analysis", "note": "Rails runner unavailable"}
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
        result_dict = output.model_dump()
        if fallback_metadata is not None:
            result_dict["_metadata"] = fallback_metadata
        return json.dumps(result_dict, ensure_ascii=False, indent=2)


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


def _model_name_to_path(model_name: str) -> str:
    """'Admin::Company' -> 'admin/company.rb'"""
    parts = model_name.split("::")
    underscored = []
    for part in parts:
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", part)
        s2 = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1)
        underscored.append(s2.lower())
    return "/".join(underscored) + ".rb"


def _fallback_trace_callbacks(
    config: Any,
    params: TraceCallbackChainInput,
) -> dict[str, Any]:
    """Rails runner が使えない場合にモデルファイルからコールバックを静的解析する"""
    model_name = params.model_name
    lifecycle_event = params.lifecycle_event
    rel_path = _model_name_to_path(model_name)
    model_file = Path(config.rails_project_path) / "app" / "models" / rel_path

    execution_order: list[dict[str, Any]] = []

    if model_file.exists():
        content = model_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        callback_re = re.compile(r"^\s*(before|after|around)_(\w+)\s+:(\w+)(.*)")
        order = 0
        for lineno, line in enumerate(lines, start=1):
            m = callback_re.match(line)
            if not m:
                continue
            kind = m.group(1)
            event = m.group(2)
            method_name = m.group(3)
            rest = m.group(4) or ""

            if event != lifecycle_event:
                continue

            conditions: dict[str, Any] = {}
            if_m = re.search(r"if:\s*:(\w+)", rest)
            unless_m = re.search(r"unless:\s*:(\w+)", rest)
            if if_m:
                conditions["if"] = if_m.group(1)
            if unless_m:
                conditions["unless"] = unless_m.group(1)

            order += 1
            execution_order.append({
                "order": order,
                "kind": kind,
                "event": event,
                "method_name": method_name,
                "source_file": str(model_file),
                "source_line": lineno,
                "conditions": conditions,
                "defined_in_concern": None,
            })

    return {
        "model_name": model_name,
        "lifecycle_event": lifecycle_event,
        "execution_order": execution_order,
    }

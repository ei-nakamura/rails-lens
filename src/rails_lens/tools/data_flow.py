"""rails_lens_data_flow ツール"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import (
    CallbackTransform,
    DataFlowInput,
    DataFlowOutput,
    DataFlowStep,
    ErrorResponse,
    RouteInfo,
    StrongParamsInfo,
)


def _extract_strong_params(grep_results: list[Any], controller: str) -> StrongParamsInfo | None:
    """GrepSearchの結果からStrong Parametersを抽出する"""
    for match in grep_results:
        line_text = match.context.match if match.context else ""
        # permit( pattern
        permit_match = re.search(r'\.permit\(([^)]+)\)', line_text)
        if not permit_match:
            continue

        params_str = permit_match.group(1)
        # Extract symbol parameters: :name, :email, etc.
        permitted = re.findall(r':(\w+)', params_str)
        # Extract nested parameters: association_attributes: [:field, ...]
        nested: dict[str, list[str]] = {}
        nested_matches = re.finditer(r':(\w+_attributes)\s*=>\s*\[([^\]]*)\]', params_str)
        for nm in nested_matches:
            assoc_key = nm.group(1)
            nested_fields = re.findall(r':(\w+)', nm.group(2))
            nested[assoc_key] = nested_fields

        return StrongParamsInfo(
            file=match.file,
            line=match.line,
            permitted_params=permitted,
            nested_params=nested,
        )
    return None


def _generate_mermaid_sequence(output: DataFlowOutput) -> str:
    """sequenceDiagram形式のMermaid図を生成する"""
    lines = ["sequenceDiagram"]
    lines.append("    participant Client")
    lines.append("    participant Router")

    entry = output.entry_point
    if "#" in entry:
        controller_part, action_part = entry.split("#", 1)
    else:
        controller_part = entry + "Controller"
        action_part = "action"

    lines.append(f"    participant Controller as {controller_part}")
    lines.append("    participant Params as StrongParameters")
    lines.append("    participant Model")
    lines.append("    participant DB")
    lines.append("")

    if output.route:
        lines.append(
            f"    Client->>Router: {output.route.verb} {output.route.path}"
        )
        lines.append(f"    Router->>Controller: #{action_part}")
    else:
        lines.append("    Client->>Router: HTTP request")
        lines.append(f"    Router->>Controller: #{action_part}")

    if output.strong_params:
        permitted = ", ".join(f":{p}" for p in output.strong_params.permitted_params[:5])
        lines.append(
            f"    Controller->>Params: params.require(...).permit({permitted})"
        )
        lines.append("    Params->>Model: Model.new(permitted_params)")
    else:
        lines.append("    Controller->>Model: Model.new(params)")

    for cb in output.callbacks:
        lines.append(f"    Model->>Model: {cb.kind} :{cb.method_name}")

    lines.append("    Model->>DB: SQL query")

    return "\n".join(lines)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_data_flow",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def data_flow(params: DataFlowInput) -> str:
        """HTTPリクエストからDB保存までのデータフローを可視化する

        ルーティング→Strong Parameters→コールバックの順で解析する。
        """
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        if not params.controller_action and not params.model_name:
            return ErrorResponse(
                code="INVALID_INPUT",
                message="Either controller_action or model_name must be specified",
            ).model_dump_json(indent=2)

        try:
            # ランタイム解析 (Ruby)
            identifier = params.controller_action or params.model_name or ""
            raw_data = await bridge.execute(
                "data_flow.rb",
                args=[identifier, ""],
            )
        except Exception as e:
            return ErrorResponse(
                code="RUNTIME_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            routes_raw = raw_data.get("routes", [])
            callbacks_raw = raw_data.get("callbacks", [])

            route: RouteInfo | None = None
            if routes_raw:
                r = routes_raw[0]
                route = RouteInfo(
                    verb=r.get("verb", ""),
                    path=r.get("path", ""),
                    controller=r.get("controller", ""),
                    action=r.get("action", ""),
                )

            callbacks = [
                CallbackTransform(
                    kind=cb.get("kind", ""),
                    method_name=cb.get("method_name", ""),
                    file=cb.get("file", ""),
                    line=cb.get("line", 0),
                    description=cb.get("description", ""),
                )
                for cb in callbacks_raw
            ]

            # 静的解析: Strong Parameters を controllers/ から抽出
            strong_params: StrongParamsInfo | None = None
            try:
                controller_scope = "controllers"
                search_query = "permit("
                grep_results = grep.search(search_query, scope=controller_scope, search_type="any")
                strong_params = _extract_strong_params(grep_results, identifier)
            except Exception:
                pass

            # flow_steps 組み立て
            flow_steps: list[DataFlowStep] = []
            step_order = 1

            if route:
                flow_steps.append(DataFlowStep(
                    order=step_order,
                    layer="routing",
                    description=f"{route.verb} {route.path} → {route.controller}#{route.action}",
                    details={"verb": route.verb, "path": route.path},
                ))
                step_order += 1

            if strong_params:
                flow_steps.append(DataFlowStep(
                    order=step_order,
                    layer="strong_params",
                    description=f"permit({', '.join(strong_params.permitted_params[:5])})",
                    file=strong_params.file,
                    line=strong_params.line,
                ))
                step_order += 1

            flow_steps.append(DataFlowStep(
                order=step_order,
                layer="assignment",
                description="Model.new(permitted_params) — attribute assignment",
            ))
            step_order += 1

            for cb in callbacks:
                flow_steps.append(DataFlowStep(
                    order=step_order,
                    layer="callback",
                    description=cb.description,
                    file=cb.file or None,
                    line=cb.line or None,
                ))
                step_order += 1

            flow_steps.append(DataFlowStep(
                order=step_order,
                layer="db",
                description="SQL INSERT/UPDATE via ActiveRecord",
            ))

            output = DataFlowOutput(
                entry_point=identifier,
                attribute=params.attribute,
                route=route,
                strong_params=strong_params,
                callbacks=callbacks,
                flow_steps=flow_steps,
                mermaid_diagram="",
            )
            output.mermaid_diagram = _generate_mermaid_sequence(output)

            return output.model_dump_json(indent=2)

        except Exception as e:
            return ErrorResponse(
                code="DATA_FLOW_ERROR", message=str(e)
            ).model_dump_json(indent=2)

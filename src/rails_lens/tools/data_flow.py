"""rails_lens_data_flow ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
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


def _controller_name_to_snake(name: str) -> str:
    """Convert controller name to snake_case path.

    'UsersController' -> 'users_controller'
    'Admin::UsersController' -> 'admin/users_controller'
    """
    parts = name.split("::")
    snake_parts = []
    for part in parts:
        s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', part)
        snake_parts.append(re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower())
    return "/".join(snake_parts)


def _fallback_data_flow(config: Any, params: DataFlowInput) -> dict[str, Any]:
    """Rails runner不使用時: routes.rb解析 + controllerファイルのアクション抽出 + モデル参照検出"""
    project_path = config.rails_project_path
    identifier = params.controller_action or params.model_name or ""

    # 1. routes.rb からルート→コントローラ対応を抽出
    route: RouteInfo | None = None
    routes_file = project_path / "config" / "routes.rb"
    if routes_file.exists():
        try:
            content = routes_file.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(
                r'(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]'
                r'.*?to:\s*[\'"](\w+)#(\w+)[\'"]',
                content,
                re.IGNORECASE,
            ):
                verb = m.group(1).upper()
                path = m.group(2)
                controller = m.group(3)
                action = m.group(4)
                if not route:
                    route = RouteInfo(verb=verb, path=path, controller=controller, action=action)
                    break
        except OSError:
            pass

    # 2. app/controllers/ 配下のコントローラファイルのアクションメソッドを抽出
    actions_found: list[str] = []
    model_refs_found: list[str] = []
    controllers_dir = project_path / "app" / "controllers"
    ctrl_files: list[Path] = []

    if controllers_dir.exists():
        if params.controller_action and "#" in params.controller_action:
            ctrl_name = params.controller_action.split("#")[0]
            snake = _controller_name_to_snake(ctrl_name)
            f = controllers_dir / f"{snake}.rb"
            if f.exists():
                ctrl_files = [f]

        if not ctrl_files and params.model_name:
            model_snake = _controller_name_to_snake(params.model_name)
            for suffix in [f"{model_snake}s_controller.rb", f"{model_snake}_controller.rb"]:
                f = controllers_dir / suffix
                if f.exists():
                    ctrl_files = [f]
                    break

        if not ctrl_files:
            ctrl_files = list(controllers_dir.rglob("*_controller.rb"))[:3]

        for ctrl_file in ctrl_files[:2]:
            try:
                ctrl_content = ctrl_file.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'^\s*def\s+(\w+)', ctrl_content, re.MULTILINE):
                    actions_found.append(m.group(1))
                for m in re.finditer(
                    r'\b([A-Z][A-Za-z]+)\.(find|where|new|create|all|first|last)\b',
                    ctrl_content,
                ):
                    model_refs_found.append(m.group(1))
            except OSError:
                pass

    # 3. DataFlowOutput 構築
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
    flow_steps.append(DataFlowStep(
        order=step_order,
        layer="assignment",
        description="Model.new(params) — attribute assignment (file analysis)",
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
        strong_params=None,
        callbacks=[],
        flow_steps=flow_steps,
        mermaid_diagram="",
    )
    output.mermaid_diagram = _generate_mermaid_sequence(output)

    result = output.model_dump()
    result["_metadata"] = {
        "source": "file_analysis",
        "note": (
            "Full data flow requires Rails runner. "
            "routes.rb + controllers analyzed statically."
        ),
        "actions_found": list(dict.fromkeys(actions_found))[:10],
        "model_refs_found": list(dict.fromkeys(model_refs_found))[:10],
    }
    return result


async def data_flow_impl(
    params: DataFlowInput,
    bridge: Any,
    grep: Any,
) -> DataFlowOutput:
    """MCPデコレータなしで同じロジックを実行し、DataFlowOutput を返す"""
    identifier = params.controller_action or params.model_name or ""
    raw_data = await bridge.execute(
        "data_flow.rb",
        args=[identifier, ""],
    )

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

    strong_params: StrongParamsInfo | None = None
    try:
        grep_results = grep.search("permit(", scope="controllers", search_type="any")
        strong_params = _extract_strong_params(grep_results, identifier)
    except Exception:
        pass

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
    return output


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
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            fallback = _fallback_data_flow(config, params)
            return json.dumps(fallback, ensure_ascii=False, indent=2)
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

"""rails_lens_dependency_graph ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError
from rails_lens.models import (
    DependencyGraphInput,
    DependencyGraphOutput,
    ErrorResponse,
    GraphEdge,
    GraphNode,
)


async def dependency_graph_impl(
    params: DependencyGraphInput,
    bridge: Any,
) -> DependencyGraphOutput:
    """MCPデコレータなしで同じロジックを実行し、DependencyGraphOutput を返す"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    visited: set[str] = set()
    max_depth = min(params.depth, 3)

    async def explore(model_name: str, current_depth: int) -> None:
        if model_name in visited or current_depth > max_depth:
            return
        visited.add(model_name)
        try:
            raw_data = await bridge.execute("introspect_model.rb", args=[model_name])
        except RailsLensError:
            return
        file_path = raw_data.get("file_path", "")
        nodes.append(GraphNode(id=model_name, type="model", file_path=file_path))
        for assoc in raw_data.get("associations", []):
            target = assoc.get("class_name", "")
            if not target:
                continue
            edges.append(GraphEdge.model_validate({
                "from": model_name,
                "to": target,
                "relation": "association",
                "label": assoc.get("type", ""),
            }))
            if current_depth < max_depth:
                await explore(target, current_depth + 1)

    await explore(params.entry_point, 1)
    mermaid = _generate_mermaid_graph(nodes, edges)
    return DependencyGraphOutput(
        entry_point=params.entry_point,
        depth=params.depth,
        nodes=nodes,
        edges=edges,
        mermaid_diagram=mermaid,
    )


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_dependency_graph",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def dependency_graph(params: DependencyGraphInput) -> str:
        """依存関係グラフ生成"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return _error_json("INITIALIZATION_ERROR", str(e))

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        visited: set[str] = set()
        max_depth = min(params.depth, 3)

        async def explore(model_name: str, current_depth: int) -> None:
            if model_name in visited or current_depth > max_depth:
                return
            visited.add(model_name)

            try:
                raw_data = await bridge.execute(
                    "introspect_model.rb", args=[model_name]
                )
            except RailsLensError:
                return

            nodes.append(GraphNode(
                id=model_name,
                type="model",
                file_path=raw_data.get("file_path", ""),
            ))

            for assoc in raw_data.get("associations", []):
                target = assoc.get("class_name", "")
                if not target:
                    continue
                edges.append(GraphEdge.model_validate({
                    "from": model_name,
                    "to": target,
                    "relation": "association",
                    "label": assoc.get("type", ""),
                }))
                if current_depth < max_depth:
                    await explore(target, current_depth + 1)

        try:
            await explore(params.entry_point, 1)
        except Exception as e:
            return _error_json("EXPLORATION_ERROR", str(e))

        mermaid = _generate_mermaid_graph(nodes, edges)

        output = DependencyGraphOutput(
            entry_point=params.entry_point,
            depth=params.depth,
            nodes=nodes,
            edges=edges,
            mermaid_diagram=mermaid,
        )
        return json.dumps(output.model_dump(by_alias=True), ensure_ascii=False, indent=2)


def _generate_mermaid_graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    """依存関係グラフのMermaid graph LRを生成する"""
    lines = ["graph LR"]
    for edge in edges:
        lines.append(f"    {edge.from_node} -->|{edge.label}| {edge.to_node}")
    return "\n".join(lines)


def _error_json(code: str, message: str, suggestion: str | None = None) -> str:
    """エラーレスポンスをJSON文字列として返す"""
    resp = ErrorResponse(code=code, message=message, suggestion=suggestion)
    return resp.model_dump_json(indent=2)

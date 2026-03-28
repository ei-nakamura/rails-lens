"""rails_lens_circular_dependencies ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import (
    CircularDependenciesInput,
    CircularDependenciesOutput,
    CyclePath,
    ErrorResponse,
    GraphEdge,
)


def _generate_mermaid_diagram(output: CircularDependenciesOutput) -> str:
    """循環依存を Mermaid graph LR 形式で生成する（循環ノードを強調）"""
    lines = ["graph LR"]

    # 循環に参加しているモデルをノードとして登録
    cycle_models: set[str] = set()
    for cycle in output.cycles:
        cycle_models.update(cycle.models)

    for model in sorted(cycle_models):
        lines.append(f'  {model}["{model}"]')

    # 循環ごとに辺を追加
    added_edges: set[tuple[str, str]] = set()
    for cycle in output.cycles:
        severity_color = "#f88" if cycle.severity == "critical" else "#fa4"
        for edge in cycle.edges:
            key = (edge.from_node, edge.to_node)
            if key not in added_edges:
                added_edges.add(key)
                label = edge.label[:30] if edge.label else edge.relation
                lines.append(f'  {edge.from_node} -->|"{label}"| {edge.to_node}')

        # 循環ノードをスタイル付け
        for model in cycle.models:
            lines.append(f"  style {model} fill:{severity_color}")

    return "\n".join(lines)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_circular_dependencies",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def detect_circular_dependencies(params: CircularDependenciesInput) -> str:
        """モデル間の循環依存（コールバック相互更新・双方向association）を検出し、Mermaid図で可視化する"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            entry_arg = params.entry_point or ""
            raw = await bridge.execute(
                "circular_dependencies.rb",
                args=[entry_arg],
            )
        except Exception as e:
            return ErrorResponse(
                code="RUNTIME_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        # CyclePath 構築（GraphEdge を model_validate 経由で構築）
        cycles: list[CyclePath] = []
        for c in raw.get("cycles", []):
            edges = [
                GraphEdge.model_validate(e)
                for e in c.get("edges", [])
            ]
            cycles.append(CyclePath(
                models=c["models"],
                edges=edges,
                cycle_type=c.get("cycle_type", "unknown"),
                severity=c.get("severity", "warning"),
            ))

        output = CircularDependenciesOutput(
            total_cycles=raw.get("total_cycles", len(cycles)),
            cycles=cycles,
            summary=raw.get("summary", f"{len(cycles)} cycle(s) detected"),
        )

        result = output.model_dump(by_alias=True)

        if params.format == "mermaid":
            result["mermaid_diagram"] = _generate_mermaid_diagram(output)
        else:
            result["mermaid_diagram"] = None

        return json.dumps(result, indent=2, ensure_ascii=False)

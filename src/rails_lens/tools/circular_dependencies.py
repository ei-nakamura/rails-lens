"""rails_lens_circular_dependencies ツール"""
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
    CircularDependenciesInput,
    CircularDependenciesOutput,
    CyclePath,
    ErrorResponse,
    GraphEdge,
)


def _fallback_circular_analysis(config: Any, params: CircularDependenciesInput) -> dict[str, Any]:
    """app/models/ から正規表現＋グラフ解析で循環依存を検出するファイルベースフォールバック"""
    models_dir = Path(config.rails_project_path) / "app" / "models"
    if not models_dir.exists():
        return {"total_cycles": 0, "cycles": [], "summary": "No models directory found"}

    assoc_re = re.compile(
        r'\b(has_many|belongs_to|has_one|has_and_belongs_to_many)\s+:(\w+)'
        r'(?:[^\n]*?class_name:\s*[\'"](\w+)[\'"])?'
    )
    class_re = re.compile(r'\bclass\s+(\w+)')

    # graph: model_name -> [(target_model, relation, assoc_label)]
    graph: dict[str, list[tuple[str, str, str]]] = {}

    for model_file in sorted(models_dir.rglob("*.rb")):
        try:
            content = model_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = class_re.search(content)
        if not m:
            continue
        model_name = m.group(1)
        if model_name in ("ApplicationRecord",):
            continue
        assocs: list[tuple[str, str, str]] = []
        for am in assoc_re.finditer(content):
            relation = am.group(1)
            name = am.group(2)
            class_name = am.group(3)
            if class_name:
                target = class_name
            elif relation in ("has_many", "has_and_belongs_to_many"):
                if name.endswith("ies"):
                    target = name[:-3] + "y"
                elif name.endswith("s"):
                    target = name[:-1]
                else:
                    target = name
                target = target.capitalize()
            else:
                target = name.capitalize()
            assocs.append((target, relation, name))
        graph[model_name] = assocs

    # DFS で循環検出
    seen_keys: set[frozenset[str]] = set()
    cycles: list[dict[str, Any]] = []

    def dfs(path: list[str], path_set: set[str]) -> None:
        current = path[-1]
        for target, _relation, _label in graph.get(current, []):
            if target in path_set:
                ci = path.index(target)
                cycle_nodes = path[ci:] + [target]
                key = frozenset(cycle_nodes[:-1])
                if key not in seen_keys:
                    seen_keys.add(key)
                    edges = []
                    for i in range(len(cycle_nodes) - 1):
                        fm, tm = cycle_nodes[i], cycle_nodes[i + 1]
                        for t, r, lbl in graph.get(fm, []):
                            if t == tm:
                                edges.append({"from": fm, "to": tm, "relation": r, "label": lbl})
                                break
                        else:
                            edges.append({"from": fm, "to": tm, "relation": "unknown", "label": ""})
                    cycles.append({
                        "models": cycle_nodes,
                        "edges": edges,
                        "cycle_type": "association",
                        "severity": "warning",
                    })
            elif target in graph:
                path.append(target)
                path_set.add(target)
                dfs(path, path_set)
                path.pop()
                path_set.discard(target)

    for model in sorted(graph.keys()):
        dfs([model], {model})

    entry = params.entry_point or ""
    if entry:
        cycles = [c for c in cycles if entry in c["models"]]

    return {
        "total_cycles": len(cycles),
        "cycles": cycles,
        "summary": f"{len(cycles)} cycle(s) detected (file analysis)",
    }


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


async def circular_dependencies_impl(
    params: CircularDependenciesInput,
    bridge: Any,
    config: Any = None,
) -> CircularDependenciesOutput:
    """MCPデコレータなしで同じロジックを実行し、CircularDependenciesOutput を返す"""
    entry_arg = params.entry_point or ""
    try:
        raw = await bridge.execute(
            "circular_dependencies.rb",
            args=[entry_arg],
        )
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        if config is not None:
            raw = _fallback_circular_analysis(config, params)
            raw["_metadata"] = {"source": "file_analysis", "note": "Rails runner unavailable"}
        else:
            raise
    cycles: list[CyclePath] = []
    for c in raw.get("cycles", []):
        edges = [GraphEdge.model_validate(e) for e in c.get("edges", [])]
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
    if params.format == "mermaid":
        output.mermaid_diagram = _generate_mermaid_diagram(output)
    return output


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

        entry_arg = params.entry_point or ""
        is_fallback = False
        try:
            raw = await bridge.execute(
                "circular_dependencies.rb",
                args=[entry_arg],
            )
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raw = _fallback_circular_analysis(config, params)
            raw["_metadata"] = {"source": "file_analysis", "note": "Rails runner unavailable"}
            is_fallback = True
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

        if is_fallback:
            result["_metadata"] = raw.get("_metadata")

        return json.dumps(result, indent=2, ensure_ascii=False)

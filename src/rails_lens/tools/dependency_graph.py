"""rails_lens_dependency_graph ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsLensError, RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import (
    DependencyGraphInput,
    DependencyGraphOutput,
    ErrorResponse,
    GraphEdge,
    GraphNode,
)

_ASSOC_RE = re.compile(
    r'\b(has_many|has_one|belongs_to|has_and_belongs_to_many)\s+:(\w+)'
    r'(?:[^\n]*?class_name:\s*[\'"](\w+)[\'"])?'
)
_CLASS_RE = re.compile(r'\bclass\s+(\w+)')


def _fallback_dependency_graph(config: Any, params: DependencyGraphInput) -> dict[str, Any]:
    """app/models/ から正規表現でアソシエーションを抽出し
    依存グラフを構築するファイルベースフォールバック"""
    models_dir = Path(config.rails_project_path) / "app" / "models"
    if not models_dir.exists():
        return {
            "entry_point": params.entry_point,
            "depth": params.depth,
            "nodes": [],
            "edges": [],
            "mermaid_diagram": "graph LR",
        }

    # model_name -> {file_path, assocs: [(target, relation, label)]}
    model_data: dict[str, dict[str, Any]] = {}

    for model_file in sorted(models_dir.rglob("*.rb")):
        try:
            content = model_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = _CLASS_RE.search(content)
        if not m:
            continue
        model_name = m.group(1)
        assocs: list[tuple[str, str, str]] = []
        for am in _ASSOC_RE.finditer(content):
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
        model_data[model_name] = {
            "file_path": str(model_file),
            "assocs": assocs,
        }

    max_depth = min(params.depth, 3)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    visited: set[str] = set()

    def explore(model_name: str, current_depth: int) -> None:
        if model_name in visited or current_depth > max_depth:
            return
        visited.add(model_name)
        file_path = model_data.get(model_name, {}).get("file_path", "")
        nodes.append({"id": model_name, "type": "model", "file_path": file_path})
        for target, relation, _label in model_data.get(model_name, {}).get("assocs", []):
            edges.append({
                "from": model_name, "to": target, "relation": "association", "label": relation,
            })
            if current_depth < max_depth:
                explore(target, current_depth + 1)

    explore(params.entry_point, 1)

    node_objs = [GraphNode(**n) for n in nodes]
    edge_objs = [GraphEdge.model_validate(e) for e in edges]
    mermaid = _generate_mermaid_graph(node_objs, edge_objs)

    return {
        "entry_point": params.entry_point,
        "depth": params.depth,
        "nodes": nodes,
        "edges": edges,
        "mermaid_diagram": mermaid,
    }


async def dependency_graph_impl(
    params: DependencyGraphInput,
    bridge: Any,
    config: Any = None,
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
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raise
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

    try:
        await explore(params.entry_point, 1)
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        if config is not None:
            raw = _fallback_dependency_graph(config, params)
            node_objs = [GraphNode(**n) for n in raw["nodes"]]
            edge_objs = [GraphEdge.model_validate(e) for e in raw["edges"]]
            return DependencyGraphOutput(
                entry_point=params.entry_point,
                depth=params.depth,
                nodes=node_objs,
                edges=edge_objs,
                mermaid_diagram=raw["mermaid_diagram"],
            )
        else:
            raise

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
            except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
                raise
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

        is_fallback = False
        fallback_raw: dict[str, Any] | None = None

        try:
            await explore(params.entry_point, 1)
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            fallback_raw = _fallback_dependency_graph(config, params)
            fallback_raw["_metadata"] = {
                "source": "file_analysis", "note": "Rails runner unavailable",
            }
            is_fallback = True
        except Exception as e:
            return _error_json("EXPLORATION_ERROR", str(e))

        if is_fallback and fallback_raw is not None:
            return json.dumps(fallback_raw, ensure_ascii=False, indent=2)

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

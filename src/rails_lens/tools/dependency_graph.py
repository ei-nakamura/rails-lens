"""rails_lens_dependency_graph ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import DependencyGraphInput, DependencyGraphOutput


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
        output = DependencyGraphOutput(entry_point=params.entry_point, depth=params.depth)
        return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)

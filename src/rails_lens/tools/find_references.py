"""rails_lens_find_references ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import FindReferencesInput, FindReferencesOutput


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_find_references",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def find_references(params: FindReferencesInput) -> str:
        """コード参照検索"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            from rails_lens.models import ErrorResponse
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        matches = grep.search(params.query, params.scope, params.type)
        output = FindReferencesOutput(
            query=params.query, total_matches=len(matches), matches=matches
        )
        return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)

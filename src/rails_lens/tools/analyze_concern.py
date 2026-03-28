"""rails_lens_analyze_concern ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_analyze_concern",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def analyze_concern(concern_name: str) -> str:
        """Rails ConcernのInclude関係・メソッドを分析"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            # Concernを使用しているモデルをgrep検索
            includers = grep.search(concern_name, scope="models", search_type="any")
            result = {
                "concern_name": concern_name,
                "included_in": [{"file": m.file, "line": m.line} for m in includers],
                "total_includers": len(includers),
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(
                code="ANALYZE_CONCERN_ERROR", message=str(e)
            ).model_dump_json(indent=2)

"""rails_lens_test_mapping ツール"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.analyzers.test_mapper import TestMapper
from rails_lens.models import ErrorResponse, TestMappingInput, TestMappingOutput


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_test_mapping",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def test_mapping(params: TestMappingInput) -> str:
        """モデルやメソッドに関連するテストファイルを検出し、実行コマンドを返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            output: TestMappingOutput = TestMapper(config).map(
                params.target, params.include_indirect
            )
            return output.model_dump_json(indent=2)
        except Exception as e:
            return ErrorResponse(
                code="TEST_MAPPING_ERROR", message=str(e)
            ).model_dump_json(indent=2)

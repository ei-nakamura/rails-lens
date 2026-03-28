"""rails_lens_list_models ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.models import ErrorResponse, ListModelsOutput, ModelSummary


async def list_models_impl(bridge: Any) -> ListModelsOutput:
    """MCPデコレータなしで同じロジックを実行し、ListModelsOutput を返す"""
    raw_data = await bridge.execute("list_models.rb", args=[])
    models = [
        ModelSummary(
            name=m.get("name", ""),
            table_name=m.get("table_name", ""),
            file_path=m.get("file_path", ""),
        )
        for m in raw_data.get("models", [])
    ]
    return ListModelsOutput(models=models)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_list_models",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def list_models() -> str:
        """Railsアプリのモデル一覧を取得"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            raw_data = await bridge.execute("list_models.rb", args=[])
            models = [
                ModelSummary(
                    name=m.get("name", ""),
                    table_name=m.get("table_name", ""),
                    file_path=m.get("file_path", ""),
                )
                for m in raw_data.get("models", [])
            ]
            output = ListModelsOutput(models=models)
            return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(
                code="LIST_MODELS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

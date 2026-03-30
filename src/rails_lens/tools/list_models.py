"""rails_lens_list_models ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse, ListModelsOutput, ModelSummary

_MODEL_CLASS_RE = re.compile(r'^\s*class\s+(\w+)\s*(?:<\s*[\w:]+)?\s*$', re.MULTILINE)
_ABSTRACT_CLASS_RE = re.compile(r'self\.abstract_class\s*=\s*true')


def _fallback_list_models(config: Any) -> dict[str, Any]:
    """app/models/ 配下の .rb をグロブしてモデル一覧を返す"""
    models_dir = config.rails_project_path / "app" / "models"
    models: list[dict[str, str]] = []
    if models_dir.exists():
        for rb_file in sorted(models_dir.rglob("*.rb")):
            try:
                content = rb_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _ABSTRACT_CLASS_RE.search(content):
                continue
            match = _MODEL_CLASS_RE.search(content)
            if not match:
                continue
            class_name = match.group(1)
            if class_name in ("ApplicationRecord",):
                continue
            stem = rb_file.stem
            table_name = stem if stem.endswith("s") else stem + "s"
            models.append({
                "name": class_name,
                "table_name": table_name,
                "file_path": str(rb_file),
            })
    return {
        "models": models,
        "_metadata": {"source": "file_analysis", "note": "Rails runner unavailable"},
    }


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
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raw_data = _fallback_list_models(config)
        except Exception as e:
            return ErrorResponse(
                code="LIST_MODELS_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            models = [
                ModelSummary(
                    name=m.get("name", ""),
                    table_name=m.get("table_name", ""),
                    file_path=m.get("file_path", ""),
                )
                for m in raw_data.get("models", [])
            ]
            output = ListModelsOutput(models=models)
            result = output.model_dump()
            if "_metadata" in raw_data:
                result["_metadata"] = raw_data["_metadata"]
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(
                code="LIST_MODELS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

"""rails_lens_get_schema ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse

_CREATE_TABLE_RE = re.compile(r'create_table\s+"(\w+)"')
_COL_TYPES = (
    r'string|integer|bigint|text|boolean|float|decimal|'
    r'datetime|date|time|binary|json|jsonb|uuid|references'
)
_COLUMN_RE = re.compile(rf't\.({_COL_TYPES})\s+"(\w+)"([^#\n]*)')
_INDEX_RE = re.compile(r'add_index\s+"(\w+)",\s+(\[.*?\]|"(\w+)")')
_INDEX_COLUMNS_RE = re.compile(r'"(\w+)"')
_UNIQUE_RE = re.compile(r'unique:\s*true')
_INDEX_NAME_RE = re.compile(r'name:\s*"([^"]+)"')
_NULL_FALSE_RE = re.compile(r'null:\s*false')

_SCHEMA_META = {"source": "file_analysis", "note": "Rails runner unavailable"}


def _fallback_get_schema(config: Any) -> dict[str, Any]:
    """db/schema.rb をテキストパースしてスキーマ情報を返す"""
    schema_path = config.rails_project_path / "db" / "schema.rb"
    if not schema_path.exists():
        return {"tables": [], "_metadata": _SCHEMA_META}

    try:
        content = schema_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"tables": [], "_metadata": _SCHEMA_META}

    tables = []
    # Split by create_table blocks
    blocks = re.split(r'(?=create_table\s+")', content)
    for block in blocks:
        table_match = _CREATE_TABLE_RE.match(block)
        if not table_match:
            continue
        table_name = table_match.group(1)
        # Extract columns
        columns = []
        for col_match in _COLUMN_RE.finditer(block):
            col_type = col_match.group(1)
            col_name = col_match.group(2)
            options = col_match.group(3)
            null_allowed = not bool(_NULL_FALSE_RE.search(options))
            columns.append({"name": col_name, "type": col_type, "null": null_allowed})

        # Extract indexes from the full content for this table
        indexes = []
        for idx_match in _INDEX_RE.finditer(content):
            if idx_match.group(1) != table_name:
                continue
            cols_str = idx_match.group(2)
            idx_columns = _INDEX_COLUMNS_RE.findall(cols_str)
            rest = idx_match.group(0)
            unique = bool(_UNIQUE_RE.search(rest))
            name_match = _INDEX_NAME_RE.search(rest)
            idx_name = (
                name_match.group(1) if name_match
                else f"index_{table_name}_on_{'_'.join(idx_columns)}"
            )
            indexes.append({"name": idx_name, "columns": idx_columns, "unique": unique})

        tables.append({"name": table_name, "columns": columns, "indexes": indexes})

    return {"tables": tables, "_metadata": _SCHEMA_META}


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_get_schema",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_schema() -> str:
        """RailsアプリのDBスキーマ情報を取得"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            cached = cache.get("get_schema", "schema")
            if cached:
                return json.dumps(cached, ensure_ascii=False, indent=2)
            try:
                raw_data = await bridge.execute("dump_schema.rb", args=[])
            except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
                raw_data = _fallback_get_schema(config)
            cache.set("get_schema", "schema", raw_data, source_files=[])
            return json.dumps(raw_data, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(code="GET_SCHEMA_ERROR", message=str(e)).model_dump_json(indent=2)

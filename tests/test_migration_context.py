"""tools/migration_context.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsRunnerExecutionError
from rails_lens.models import MigrationContextInput
from rails_lens.tools import migration_context as migration_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    migration_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_migration_context"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_migration_context_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute がスキーマ情報を返し warnings/template フィールドが存在する"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "columns": [{"name": "email", "type": "string", "null": False}],
        "indexes": [{"name": "index_users_on_email", "columns": ["email"], "unique": True}],
        "foreign_keys": [],
        "estimated_row_count": 1500,
        "migration_history": [],
    })

    params = MigrationContextInput(table_name="users")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["table_name"] == "users"
    assert "schema" in parsed
    assert "warnings" in parsed
    assert len(parsed["schema"]["columns"]) == 1
    assert parsed["schema"]["columns"][0]["name"] == "email"
    assert len(parsed["schema"]["indexes"]) == 1
    assert parsed["estimated_row_count"] == 1500
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_migration_context_large_table(mcp_and_tool) -> None:
    """estimated_row_count > 1_000_000 かつ add_index 時に high 警告が生成される"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "columns": [{"name": "id", "type": "integer", "null": False}],
        "indexes": [],
        "foreign_keys": [],
        "estimated_row_count": 5_000_000,
        "migration_history": [],
    })

    params = MigrationContextInput(table_name="events", operation="add_index")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["estimated_row_count"] == 5_000_000
    assert len(parsed["warnings"]) >= 1
    warning_types = [w["type"] for w in parsed["warnings"]]
    assert "large_table" in warning_types


@pytest.mark.asyncio
async def test_migration_context_with_operation(mcp_and_tool) -> None:
    """operation='add_column' 指定時に template が生成される"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "columns": [{"name": "email", "type": "string", "null": False}],
        "indexes": [],
        "foreign_keys": [],
        "estimated_row_count": 100,
        "migration_history": [],
    })

    params = MigrationContextInput(table_name="users", operation="add_column")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["operation"] == "add_column"
    assert parsed["template"] is not None
    assert "add_column" in parsed["template"]["code"]
    assert "users" in parsed["template"]["code"]


@pytest.mark.asyncio
async def test_migration_context_fallback_on_runner_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
    sample_rails_app: Path,
) -> None:
    """RailsRunnerExecutionError 時に db/schema.rb ファイルパースにフォールバックする"""
    schema_rb = sample_rails_app / "db" / "schema.rb"
    schema_rb.write_text(
        'ActiveRecord::Schema.define do\n'
        '  create_table "users", force: :cascade do |t|\n'
        '    t.string "email", null: false\n'
        '    t.integer "age"\n'
        '  end\n'
        '  add_index "users", ["email"], name: "index_users_on_email", unique: true\n'
        'end\n'
    )
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    migration_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_migration_context"].fn

    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))

    params = MigrationContextInput(table_name="users")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["table_name"] == "users"
    assert "schema" in parsed
    col_names = [c["name"] for c in parsed["schema"]["columns"]]
    assert "email" in col_names
    assert parsed["_metadata"]["source"] == "file_analysis"

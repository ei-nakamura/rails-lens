"""tools/get_schema.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.tools import get_schema as get_schema_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_get_deps(
    config: RailsLensConfig,
    bridge: RailsBridge,
    cache: CacheManager,
):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def tool_fn(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """get_schema ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    get_schema_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_get_schema"].fn, mock_bridge, cache_manager


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_schema_success(tool_fn) -> None:
    """正常ケース: bridge.execute の結果を返す"""
    fn, bridge, _ = tool_fn
    schema_data = {"tables": [{"name": "users", "columns": ["id", "name"]}]}
    bridge.execute = AsyncMock(return_value=schema_data)

    result = await fn()
    parsed = json.loads(result)

    assert "tables" in parsed
    assert parsed["tables"][0]["name"] == "users"
    bridge.execute.assert_called_once_with("dump_schema.rb", args=[])


@pytest.mark.asyncio
async def test_get_schema_cache_hit(tool_fn) -> None:
    """キャッシュあり時は bridge を呼ばない"""
    fn, bridge, cache = tool_fn
    schema_data = {"tables": [{"name": "posts", "columns": ["id", "title"]}]}
    cache.set("get_schema", "schema", schema_data, source_files=[])
    bridge.execute = AsyncMock()

    result = await fn()
    parsed = json.loads(result)

    assert parsed["tables"][0]["name"] == "posts"
    bridge.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_schema_bridge_error(tool_fn) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    fn, bridge, _ = tool_fn
    bridge.execute = AsyncMock(side_effect=RuntimeError("ruby error"))

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "GET_SCHEMA_ERROR"
    assert "ruby error" in parsed["message"]


@pytest.mark.asyncio
async def test_get_schema_initialization_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps() 失敗時に ErrorResponse を返す"""
    mcp = FastMCP("test")

    def failing_get_deps():
        raise RuntimeError("deps unavailable")

    get_schema_module.register(mcp, failing_get_deps)
    fn = mcp._tool_manager._tools["rails_lens_get_schema"].fn

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "deps unavailable" in parsed["message"]

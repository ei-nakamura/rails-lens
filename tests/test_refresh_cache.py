"""tools/refresh_cache.py のテスト"""
from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.tools import refresh_cache as refresh_cache_module

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
    """refresh_cache ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    refresh_cache_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_refresh_cache"].fn, cache_manager


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_cache_all(tool_fn) -> None:
    """tool_name省略時に全キャッシュ無効化する"""
    fn, cache = tool_fn
    # キャッシュにデータをセット
    cache.set("get_schema", "schema", {"tables": []}, source_files=[])
    cache.set("get_routes", "routes", {"routes": []}, source_files=[])

    result = await fn()
    parsed = json.loads(result)

    assert parsed["status"] == "ok"
    assert "All caches invalidated" in parsed["message"]
    # キャッシュが消えていること
    assert cache.get("get_schema", "schema") is None
    assert cache.get("get_routes", "routes") is None


@pytest.mark.asyncio
async def test_refresh_cache_specific(tool_fn) -> None:
    """tool_name指定時にキャッシュ無効化する"""
    fn, cache = tool_fn
    cache.set("get_schema", "schema", {"tables": []}, source_files=[])

    result = await fn(tool_name="get_schema")
    parsed = json.loads(result)

    assert parsed["status"] == "ok"
    assert "get_schema" in parsed["message"]
    assert cache.get("get_schema", "schema") is None


@pytest.mark.asyncio
async def test_refresh_cache_initialization_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps() 失敗時に ErrorResponse を返す"""
    mcp = FastMCP("test")

    def failing_get_deps():
        raise RuntimeError("deps unavailable")

    refresh_cache_module.register(mcp, failing_get_deps)
    fn = mcp._tool_manager._tools["rails_lens_refresh_cache"].fn

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "deps unavailable" in parsed["message"]

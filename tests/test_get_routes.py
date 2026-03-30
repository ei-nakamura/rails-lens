"""tools/get_routes.py のテスト"""
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
from rails_lens.tools import get_routes as get_routes_module

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
    """get_routes ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    get_routes_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_get_routes"].fn, mock_bridge, cache_manager


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_routes_success(tool_fn) -> None:
    """正常ケース: bridge.execute の結果を返す"""
    fn, bridge, _ = tool_fn
    routes_data = {"routes": [{"path": "/users", "verb": "GET", "action": "users#index"}]}
    bridge.execute = AsyncMock(return_value=routes_data)

    result = await fn()
    parsed = json.loads(result)

    assert "routes" in parsed
    assert parsed["routes"][0]["path"] == "/users"
    bridge.execute.assert_called_once_with("dump_routes.rb", args=[])


@pytest.mark.asyncio
async def test_get_routes_cache_hit(tool_fn) -> None:
    """キャッシュあり時は bridge を呼ばない"""
    fn, bridge, cache = tool_fn
    routes_data = {"routes": [{"path": "/posts", "verb": "POST", "action": "posts#create"}]}
    cache.set("get_routes", "routes", routes_data, source_files=[])
    bridge.execute = AsyncMock()

    result = await fn()
    parsed = json.loads(result)

    assert parsed["routes"][0]["path"] == "/posts"
    bridge.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_routes_bridge_error(tool_fn) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    fn, bridge, _ = tool_fn
    bridge.execute = AsyncMock(side_effect=RuntimeError("routes failed"))

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "GET_ROUTES_ERROR"
    assert "routes failed" in parsed["message"]


@pytest.mark.asyncio
async def test_get_routes_fallback_on_runner_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
    sample_rails_app: Path,
) -> None:
    """RailsRunnerExecutionError 時に config/routes.rb ファイルパースにフォールバックする"""
    routes_rb = sample_rails_app / "config" / "routes.rb"
    routes_rb.write_text(
        "Rails.application.routes.draw do\n"
        "  resources :posts\n"
        "  get '/health', to: 'health#show'\n"
        "end\n"
    )
    mcp = FastMCP("test")
    get_deps = lambda: (config, mock_bridge, cache_manager, None)  # noqa: E731
    get_routes_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_get_routes"].fn

    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))

    result = await fn()
    parsed = json.loads(result)

    assert "routes" in parsed
    assert parsed["_metadata"]["source"] == "file_analysis"
    paths = [r["path"] for r in parsed["routes"]]
    assert "/posts" in paths
    assert "/health" in paths


@pytest.mark.asyncio
async def test_get_routes_initialization_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps() 失敗時に ErrorResponse を返す"""
    mcp = FastMCP("test")

    def failing_get_deps():
        raise RuntimeError("deps unavailable")

    get_routes_module.register(mcp, failing_get_deps)
    fn = mcp._tool_manager._tools["rails_lens_get_routes"].fn

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "deps unavailable" in parsed["message"]

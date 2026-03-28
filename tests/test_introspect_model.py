"""tools/introspect_model.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsRunnerTimeoutError
from rails_lens.models import IntrospectModelInput
from rails_lens.tools import introspect_model as introspect_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    """get_deps クロージャを返す"""
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    introspect_module.register(mcp, get_deps)
    # 登録されたツール関数を取得
    tool_fn = mcp._tool_manager._tools["rails_lens_introspect_model"].fn
    return mcp, tool_fn, mock_bridge, cache_manager


@pytest.mark.asyncio
async def test_introspect_cache_hit(
    mcp_and_tool,
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """キャッシュがある場合 bridge を呼ばない"""
    _mcp, tool_fn, bridge, cache = mcp_and_tool
    cached_data = {"model_name": "User", "associations": [], "callbacks": []}
    cache.set("introspect_model", "User", cached_data)

    params = IntrospectModelInput(model_name="User")
    result = await tool_fn(params)

    parsed = json.loads(result)
    assert parsed["model_name"] == "User"
    bridge.execute.assert_not_called()


@pytest.mark.asyncio
async def test_introspect_cache_miss(
    mcp_and_tool,
) -> None:
    """キャッシュミス時に bridge を呼ぶ"""
    _mcp, tool_fn, bridge, cache = mcp_and_tool
    fixture_data = {"model_name": "User", "associations": [], "file_path": "app/models/user.rb"}
    bridge.execute = AsyncMock(return_value=fixture_data)

    params = IntrospectModelInput(model_name="User")
    result = await tool_fn(params)

    parsed = json.loads(result)
    assert parsed["model_name"] == "User"
    bridge.execute.assert_called_once()

    # キャッシュに保存されていることを確認
    cached = cache.get("introspect_model", "User")
    assert cached == fixture_data


@pytest.mark.asyncio
async def test_introspect_section_filter(
    mcp_and_tool,
) -> None:
    """sections パラメータでフィルタ"""
    _mcp, tool_fn, bridge, cache = mcp_and_tool
    full_data = {
        "model_name": "User",
        "table_name": "users",
        "file_path": "app/models/user.rb",
        "associations": [{"name": "posts"}],
        "callbacks": [{"kind": "before"}],
    }
    bridge.execute = AsyncMock(return_value=full_data)

    params = IntrospectModelInput(model_name="User", sections=["associations"])
    result = await tool_fn(params)

    parsed = json.loads(result)
    assert "associations" in parsed
    assert "callbacks" not in parsed
    # model_name, table_name, file_path は常に含まれる
    assert "model_name" in parsed


@pytest.mark.asyncio
async def test_introspect_bridge_error(
    mcp_and_tool,
) -> None:
    """bridge 例外時の ErrorResponse 返却"""
    _mcp, tool_fn, bridge, _cache = mcp_and_tool
    bridge.execute = AsyncMock(side_effect=RailsRunnerTimeoutError("timeout"))

    params = IntrospectModelInput(model_name="User")
    result = await tool_fn(params)

    parsed = json.loads(result)
    assert "code" in parsed
    assert parsed["code"] == "RUNNER_TIMEOUT"

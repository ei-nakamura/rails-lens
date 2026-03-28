"""tools/dependency_graph.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsLensError
from rails_lens.models import DependencyGraphInput
from rails_lens.tools import dependency_graph as dep_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    dep_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_dependency_graph"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_dependency_graph_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute がアソシエーションを返す"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "file_path": "app/models/user.rb",
        "associations": [
            {"class_name": "Post", "type": "has_many"},
        ],
    })

    params = DependencyGraphInput(entry_point="User", depth=1)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["entry_point"] == "User"
    assert len(parsed["nodes"]) >= 1
    assert parsed["nodes"][0]["id"] == "User"
    assert len(parsed["edges"]) == 1
    assert parsed["edges"][0]["to"] == "Post"


@pytest.mark.asyncio
async def test_dependency_graph_no_associations(mcp_and_tool) -> None:
    """アソシエーションなし: edges が空リスト"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "file_path": "app/models/user.rb",
        "associations": [],
    })

    params = DependencyGraphInput(entry_point="User", depth=1)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["entry_point"] == "User"
    assert parsed["edges"] == []
    assert len(parsed["nodes"]) == 1


@pytest.mark.asyncio
async def test_dependency_graph_mermaid_output(mcp_and_tool) -> None:
    """mermaid_diagram フィールドが存在し、graph LR を含む"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "file_path": "app/models/user.rb",
        "associations": [
            {"class_name": "Post", "type": "has_many"},
        ],
    })

    params = DependencyGraphInput(entry_point="User", depth=1)
    result = await fn(params)
    parsed = json.loads(result)

    assert "mermaid_diagram" in parsed
    assert "graph LR" in parsed["mermaid_diagram"]


@pytest.mark.asyncio
async def test_dependency_graph_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge が RailsLensError を raise した場合、explore 内でキャッチされ空グラフを返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    dep_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_dependency_graph"].fn

    params = DependencyGraphInput(entry_point="User", depth=1)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["entry_point"] == "User"
    assert parsed["nodes"] == []
    assert parsed["edges"] == []

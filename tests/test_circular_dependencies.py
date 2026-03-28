"""tools/circular_dependencies.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.models import CircularDependenciesInput
from rails_lens.tools import circular_dependencies as circ_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    circ_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_circular_dependencies"].fn
    return tool_fn, mock_bridge


_CYCLE_DATA = {
    "total_cycles": 1,
    "cycles": [
        {
            "models": ["User", "Order", "User"],
            "edges": [
                {"from": "User", "to": "Order", "relation": "has_many", "label": "orders"},
                {"from": "Order", "to": "User", "relation": "belongs_to", "label": "user"},
            ],
            "cycle_type": "association",
            "severity": "warning",
        }
    ],
    "summary": "1 cycle(s) detected",
}


@pytest.mark.asyncio
async def test_circular_deps_success(mcp_and_tool) -> None:
    """正常ケース: bridge.executeをモック、Mermaid図生成確認"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value=_CYCLE_DATA)

    params = CircularDependenciesInput(format="mermaid")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["total_cycles"] == 1
    assert len(parsed["cycles"]) == 1
    assert "User" in parsed["cycles"][0]["models"]
    assert parsed["mermaid_diagram"] is not None
    assert "graph LR" in parsed["mermaid_diagram"]
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_circular_deps_no_cycles(mcp_and_tool) -> None:
    """cycles=[] の場合: 循環なし、mermaid_diagram は None"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "total_cycles": 0,
        "cycles": [],
        "summary": "0 cycle(s) detected",
    })

    params = CircularDependenciesInput(format="json")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["total_cycles"] == 0
    assert parsed["cycles"] == []
    assert parsed["mermaid_diagram"] is None


@pytest.mark.asyncio
async def test_circular_deps_specific_model(mcp_and_tool) -> None:
    """entry_point(model_name) 指定の場合: bridge.execute に entry_point が渡される"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value=_CYCLE_DATA)

    params = CircularDependenciesInput(entry_point="User", format="mermaid")
    result = await fn(params)
    parsed = json.loads(result)

    # bridge.execute が entry_point="User" の args で呼ばれることを確認
    bridge.execute.assert_called_once_with("circular_dependencies.rb", args=["User"])
    assert parsed["total_cycles"] == 1

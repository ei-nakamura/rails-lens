"""tools/analyze_concern.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.models import MatchContext, ReferenceMatch
from rails_lens.tools import analyze_concern as analyze_concern_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_get_deps(
    config: RailsLensConfig,
    bridge: RailsBridge,
    cache: CacheManager,
    grep: object,
):
    def get_deps():
        return config, bridge, cache, grep
    return get_deps


@pytest.fixture
def grep_mock():
    """GrepSearch の search() をモック"""
    mock = MagicMock()
    mock.search.return_value = []
    return mock


@pytest.fixture
def tool_fn(
    config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager, grep_mock
):
    """analyze_concern ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager, grep_mock)
    analyze_concern_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_analyze_concern"].fn, grep_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_concern_success(tool_fn) -> None:
    """正常ケース: grep.search がIncluderを返す"""
    fn, grep = tool_fn
    grep.search.return_value = [
        ReferenceMatch(
            file="app/models/user.rb",
            line=3,
            context=MatchContext(match="include Auditable"),
            match_type="any",
        )
    ]

    result = await fn(concern_name="Auditable")
    parsed = json.loads(result)

    assert parsed["concern_name"] == "Auditable"
    assert parsed["total_includers"] == 1
    assert parsed["included_in"][0]["file"] == "app/models/user.rb"
    assert parsed["included_in"][0]["line"] == 3


@pytest.mark.asyncio
async def test_analyze_concern_no_includers(tool_fn) -> None:
    """Includerなし: included_in が空リスト"""
    fn, grep = tool_fn
    grep.search.return_value = []

    result = await fn(concern_name="UnusedConcern")
    parsed = json.loads(result)

    assert parsed["concern_name"] == "UnusedConcern"
    assert parsed["total_includers"] == 0
    assert parsed["included_in"] == []


@pytest.mark.asyncio
async def test_analyze_concern_initialization_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps() 失敗時に ErrorResponse を返す"""
    mcp = FastMCP("test")

    def failing_get_deps():
        raise RuntimeError("deps unavailable")

    analyze_concern_module.register(mcp, failing_get_deps)
    fn = mcp._tool_manager._tools["rails_lens_analyze_concern"].fn

    result = await fn(concern_name="Auditable")
    parsed = json.loads(result)

    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "deps unavailable" in parsed["message"]

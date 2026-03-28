"""tools/find_references.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.models import FindReferencesInput, MatchContext, ReferenceMatch
from rails_lens.tools import find_references as find_references_module

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
    """find_references ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager, grep_mock)
    find_references_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_find_references"].fn, grep_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_references_success(tool_fn) -> None:
    """正常ケース: grep.search がマッチを返す"""
    fn, grep = tool_fn
    grep.search.return_value = [
        ReferenceMatch(
            file="app/models/user.rb",
            line=1,
            context=MatchContext(match="class User < ApplicationRecord"),
            match_type="class_reference",
        )
    ]

    params = FindReferencesInput(query="User", scope="models", type="class")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["query"] == "User"
    assert parsed["total_matches"] == 1
    assert len(parsed["matches"]) == 1
    assert parsed["matches"][0]["file"] == "app/models/user.rb"


@pytest.mark.asyncio
async def test_find_references_no_results(tool_fn) -> None:
    """結果なし: matches が空リスト"""
    fn, grep = tool_fn
    grep.search.return_value = []

    params = FindReferencesInput(query="NonExistent")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["total_matches"] == 0
    assert parsed["matches"] == []


@pytest.mark.asyncio
async def test_find_references_initialization_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps() 失敗時に ErrorResponse を返す"""
    mcp = FastMCP("test")

    def failing_get_deps():
        raise RuntimeError("deps unavailable")

    find_references_module.register(mcp, failing_get_deps)
    fn = mcp._tool_manager._tools["rails_lens_find_references"].fn

    params = FindReferencesInput(query="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "deps unavailable" in parsed["message"]

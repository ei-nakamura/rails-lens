"""tools/list_models.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.tools import list_models as list_models_module

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    import json
    with open(FIXTURES_DIR / "ruby_output" / name) as f:
        return json.load(f)


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
    """list_models ツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    list_models_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_list_models"].fn, mock_bridge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_models_success(tool_fn) -> None:
    """正常ケース: fixtures/ruby_output/list_models.json のデータを返す"""
    fn, bridge = tool_fn
    fixture_data = _load_fixture("list_models.json")
    bridge.execute = AsyncMock(return_value=fixture_data)

    result = await fn()
    parsed = json.loads(result)

    assert "models" in parsed
    assert len(parsed["models"]) == 2
    names = [m["name"] for m in parsed["models"]]
    assert "Post" in names
    assert "User" in names


@pytest.mark.asyncio
async def test_list_models_empty(tool_fn) -> None:
    """モデルなし: models が空リスト"""
    fn, bridge = tool_fn
    bridge.execute = AsyncMock(return_value={"models": []})

    result = await fn()
    parsed = json.loads(result)

    assert parsed["models"] == []


@pytest.mark.asyncio
async def test_list_models_bridge_error(tool_fn) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    fn, bridge = tool_fn
    bridge.execute = AsyncMock(side_effect=RuntimeError("ruby crashed"))

    result = await fn()
    parsed = json.loads(result)

    assert parsed["code"] == "LIST_MODELS_ERROR"
    assert "ruby crashed" in parsed["message"]

"""tools/gem_introspect.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from conftest import load_fixture
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsLensError, RailsRunnerExecutionError
from rails_lens.models import GemIntrospectInput
from rails_lens.tools import gem_introspect as gem_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    gem_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_gem_introspect"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_gem_introspect_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute が gem_methods を返す"""
    fn, bridge = mcp_and_tool
    fixture = load_fixture("gem_introspect_user.json")
    bridge.execute = AsyncMock(return_value=fixture)

    params = GemIntrospectInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert len(parsed["gem_methods"]) == 1
    assert parsed["gem_methods"][0]["gem_name"] == "devise"
    assert parsed["gem_methods"][0]["method_name"] == "authenticate!"
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_gem_introspect_empty(mcp_and_tool) -> None:
    """Gem 影響なし: gem_methods/gem_callbacks/gem_routes が全て空リスト"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "model_name": "User",
        "gem_methods": [],
        "gem_callbacks": [],
        "gem_routes": [],
    })

    params = GemIntrospectInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["gem_methods"] == []
    assert parsed["gem_callbacks"] == []
    assert parsed["gem_routes"] == []


@pytest.mark.asyncio
async def test_gem_introspect_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    gem_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_gem_introspect"].fn

    params = GemIntrospectInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert "message" in parsed
    assert "bridge failed" in parsed["message"]


@pytest.mark.asyncio
async def test_gem_introspect_fallback(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """RailsRunnerExecutionError 時にGemfile解析フォールバックを返す"""
    # Gemfile を更新
    gemfile = config.rails_project_path / "Gemfile"
    gemfile.write_text(
        "source 'https://rubygems.org'\n"
        "gem 'rails', '~> 7.0'\n"
        "gem 'devise', '4.9.3'\n"
        "group :development, :test do\n"
        "  gem 'rspec-rails'\n"
        "end\n"
    )
    # Gemfile.lock を作成
    (config.rails_project_path / "Gemfile.lock").write_text(
        "GEM\n"
        "  remote: https://rubygems.org/\n"
        "  specs:\n"
        "    devise (4.9.3)\n"
        "    rails (7.0.8)\n"
        "    rspec-rails (6.0.3)\n"
    )

    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    gem_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_gem_introspect"].fn

    params = GemIntrospectInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["_metadata"]["source"] == "file_analysis"
    assert parsed["model_name"] == "User"
    assert parsed["_metadata"]["lockfile_available"] is True
    gem_names = [g["gem_name"] for g in parsed["gem_methods"]]
    assert "rails" in gem_names
    assert "devise" in gem_names
    assert "rspec-rails" in gem_names
    # Gemfile.lock のバージョンが反映される
    devise_entry = next(g for g in parsed["gem_methods"] if g["gem_name"] == "devise")
    assert "4.9.3" in devise_entry["method_name"]

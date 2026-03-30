"""tools/explain_method_resolution.py のテスト"""
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
from rails_lens.models import MethodResolutionInput
from rails_lens.tools import explain_method_resolution as method_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    method_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_explain_method_resolution"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_method_resolution_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute が ancestors を返す"""
    fn, bridge = mcp_and_tool
    fixture = load_fixture("method_resolution_user.json")
    bridge.execute = AsyncMock(return_value=fixture)

    params = MethodResolutionInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert len(parsed["ancestors"]) == 3
    assert parsed["ancestors"][0]["name"] == "User"
    assert parsed["ancestors"][0]["type"] == "self"
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_method_resolution_with_method_name(mcp_and_tool) -> None:
    """method_name 指定時: bridge.execute の呼び出し引数に method_name が含まれる"""
    fn, bridge = mcp_and_tool
    fixture = load_fixture("method_resolution_user.json")
    fixture = dict(
        fixture,
        method_owner="Authenticatable",
        super_chain=["Authenticatable#authenticate"],
    )
    bridge.execute = AsyncMock(return_value=fixture)

    params = MethodResolutionInput(model_name="User", method_name="authenticate")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["method_owner"] == "Authenticatable"
    assert "Authenticatable#authenticate" in parsed["super_chain"]
    call_args = bridge.execute.call_args
    assert "authenticate" in call_args.args or "authenticate" in str(call_args)


@pytest.mark.asyncio
async def test_method_resolution_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    method_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_explain_method_resolution"].fn

    params = MethodResolutionInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert "message" in parsed
    assert "bridge failed" in parsed["message"]


@pytest.mark.asyncio
async def test_method_resolution_fallback(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """RailsRunnerExecutionError 時にファイルベースフォールバックを返す"""
    # user.rb に include/extend/prepend を追加
    user_rb = config.rails_project_path / "app" / "models" / "user.rb"
    user_rb.write_text(
        "class User < ApplicationRecord\n"
        "  prepend Overridable\n"
        "  include Devise::Authenticatable\n"
        "  extend ClassMethods\n"
        "end\n"
    )

    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    method_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_explain_method_resolution"].fn

    params = MethodResolutionInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["_metadata"]["source"] == "file_analysis"
    assert parsed["model_name"] == "User"
    ancestor_names = [a["name"] for a in parsed["ancestors"]]
    assert "Overridable" in ancestor_names
    assert "Devise::Authenticatable" in ancestor_names
    assert "ClassMethods" in ancestor_names
    assert "User" in ancestor_names
    assert "ApplicationRecord" in ancestor_names
    # prepend: Overridable は User より前に来る
    assert ancestor_names.index("Overridable") < ancestor_names.index("User")

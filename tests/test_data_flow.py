"""tools/data_flow.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsLensError, RailsRunnerExecutionError
from rails_lens.models import DataFlowInput
from rails_lens.tools import data_flow as data_flow_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    data_flow_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_data_flow"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_data_flow_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute が routes/callbacks を返し mermaid sequenceDiagram を含む"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "routes": [
            {"verb": "POST", "path": "/users", "controller": "users", "action": "create"}
        ],
        "callbacks": [
            {
                "kind": "before_save",
                "method_name": "normalize_email",
                "file": "app/models/user.rb",
                "line": 10,
                "description": "before_save :normalize_email",
            }
        ],
    })

    params = DataFlowInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["entry_point"] == "User"
    assert "mermaid_diagram" in parsed
    assert "sequenceDiagram" in parsed["mermaid_diagram"]
    assert parsed["route"]["verb"] == "POST"
    assert parsed["route"]["path"] == "/users"
    assert len(parsed["callbacks"]) == 1
    assert parsed["callbacks"][0]["method_name"] == "normalize_email"
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_data_flow_with_action(mcp_and_tool) -> None:
    """controller_action 指定時: entry_point に controller_action が設定される"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "routes": [
            {"verb": "POST", "path": "/users", "controller": "users", "action": "create"}
        ],
        "callbacks": [],
    })

    params = DataFlowInput(controller_action="UsersController#create")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["entry_point"] == "UsersController#create"
    assert "sequenceDiagram" in parsed["mermaid_diagram"]
    assert parsed["route"]["action"] == "create"


@pytest.mark.asyncio
async def test_data_flow_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    data_flow_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_data_flow"].fn

    params = DataFlowInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert "message" in parsed
    assert "bridge failed" in parsed["message"]


@pytest.mark.asyncio
async def test_data_flow_fallback(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """RailsRunnerExecutionError 時にファイルベースフォールバックを返す"""
    # routes.rb を作成
    routes_rb = config.rails_project_path / "config" / "routes.rb"
    routes_rb.write_text(
        "Rails.application.routes.draw do\n"
        "  post '/users', to: 'users#create'\n"
        "end\n"
    )
    # controller を作成
    controllers_dir = config.rails_project_path / "app" / "controllers"
    controllers_dir.mkdir(parents=True, exist_ok=True)
    (controllers_dir / "users_controller.rb").write_text(
        "class UsersController < ApplicationController\n"
        "  def create\n"
        "    @user = User.new(user_params)\n"
        "    @user.save\n"
        "  end\n"
        "  def index\n"
        "    @users = User.all\n"
        "  end\n"
        "end\n"
    )

    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    data_flow_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_data_flow"].fn

    params = DataFlowInput(model_name="User")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["_metadata"]["source"] == "file_analysis"
    assert parsed["entry_point"] == "User"
    assert "sequenceDiagram" in parsed["mermaid_diagram"]
    assert parsed["route"]["verb"] == "POST"
    assert parsed["route"]["path"] == "/users"
    assert "User" in parsed["_metadata"]["model_refs_found"]
    assert "create" in parsed["_metadata"]["actions_found"]

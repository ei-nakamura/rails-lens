"""tools/trace_callback_chain.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsLensError, RailsRunnerExecutionError
from rails_lens.models import TraceCallbackChainInput
from rails_lens.tools import trace_callback_chain as trace_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    trace_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_trace_callback_chain"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_trace_callback_chain_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute が execution_order を返す"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "model_name": "User",
        "lifecycle_event": "save",
        "execution_order": [
            {
                "order": 1,
                "kind": "before",
                "event": "save",
                "method_name": "normalize_email",
                "source_file": "app/models/user.rb",
                "source_line": 10,
                "conditions": {},
                "defined_in_concern": None,
            }
        ],
    })

    params = TraceCallbackChainInput(model_name="User", lifecycle_event="save")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["lifecycle_event"] == "save"
    assert len(parsed["execution_order"]) == 1
    assert parsed["execution_order"][0]["method_name"] == "normalize_email"
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_trace_callback_chain_empty(mcp_and_tool) -> None:
    """コールバックなし: execution_order が空リスト"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "model_name": "User",
        "lifecycle_event": "save",
        "execution_order": [],
    })

    params = TraceCallbackChainInput(model_name="User", lifecycle_event="save")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["execution_order"] == []


@pytest.mark.asyncio
async def test_trace_callback_chain_mermaid_output(mcp_and_tool) -> None:
    """mermaid_diagram フィールドが存在し、sequenceDiagram を含む"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "model_name": "User",
        "lifecycle_event": "save",
        "execution_order": [
            {
                "order": 1,
                "kind": "before",
                "event": "save",
                "method_name": "normalize_email",
                "source_file": "app/models/user.rb",
                "source_line": 10,
                "conditions": {},
                "defined_in_concern": None,
            }
        ],
    })

    params = TraceCallbackChainInput(model_name="User", lifecycle_event="save")
    result = await fn(params)
    parsed = json.loads(result)

    assert "mermaid_diagram" in parsed
    assert "sequenceDiagram" in parsed["mermaid_diagram"]


@pytest.mark.asyncio
async def test_trace_callback_chain_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    trace_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_trace_callback_chain"].fn

    params = TraceCallbackChainInput(model_name="User", lifecycle_event="save")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert "message" in parsed
    assert "bridge failed" in parsed["message"]


@pytest.mark.asyncio
async def test_trace_callback_chain_fallback_on_runner_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
    sample_rails_app: Path,
) -> None:
    """bridge が RailsRunnerExecutionError を返したときファイルベースフォールバックを使う"""
    model_file = sample_rails_app / "app" / "models" / "user.rb"
    model_file.write_text(
        "class User < ApplicationRecord\n"
        "  before_save :normalize_email\n"
        "  after_save :send_notification\n"
        "  before_create :set_defaults\n"
        "  before_save :audit_changes, if: :changed?\n"
        "end\n"
    )
    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("runner failed"))

    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    trace_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_trace_callback_chain"].fn

    params = TraceCallbackChainInput(model_name="User", lifecycle_event="save")
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["_metadata"]["source"] == "file_analysis"
    assert parsed["model_name"] == "User"
    assert parsed["lifecycle_event"] == "save"
    # before_save x2, after_save x1
    assert len(parsed["execution_order"]) == 3
    kinds = {cb["kind"] for cb in parsed["execution_order"]}
    assert "before" in kinds
    assert "after" in kinds

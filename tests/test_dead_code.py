"""tools/dead_code.py のテスト"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.models import DeadCodeInput, DeadCodeItem
from rails_lens.tools import dead_code as dead_code_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    dead_code_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_dead_code"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_dead_code_success(mcp_and_tool) -> None:
    """正常ケース: bridge.executeとDeadCodeDetector両方をモック"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={
        "excluded_methods": ["normalize_email", "active", "recent"],
    })

    mock_item = DeadCodeItem(
        type="instance_method",
        name="unused_method",
        file="app/models/user.rb",
        line=42,
        confidence="high",
        reason="No references found",
        reference_count=0,
        dynamic_call_risk=False,
    )

    with patch("rails_lens.tools.dead_code.DeadCodeDetector") as mock_cls:
        mock_detector = mock_cls.return_value
        mock_detector.detect.return_value = ([mock_item], 10)

        params = DeadCodeInput(model_name="User", scope="models", confidence="high")
        result = await fn(params)
        parsed = json.loads(result)

    assert parsed["scope"] == "models"
    assert parsed["model_name"] == "User"
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["name"] == "unused_method"
    assert parsed["items"][0]["confidence"] == "high"
    assert parsed["total_methods_analyzed"] == 10
    assert parsed["total_dead_code_found"] == 1
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_dead_code_confidence_levels(mcp_and_tool) -> None:
    """confidence (high/medium) 判定確認"""
    fn, bridge = mcp_and_tool
    bridge.execute = AsyncMock(return_value={"excluded_methods": []})

    high_item = DeadCodeItem(
        type="instance_method",
        name="unused_high",
        file="app/models/user.rb",
        line=10,
        confidence="high",
        reason="No references found",
        reference_count=0,
        dynamic_call_risk=False,
    )
    medium_item = DeadCodeItem(
        type="instance_method",
        name="unused_medium",
        file="app/models/user.rb",
        line=20,
        confidence="medium",
        reason="Dynamic call possible via send/public_send",
        reference_count=0,
        dynamic_call_risk=True,
    )

    # high confidence フィルタ
    with patch("rails_lens.tools.dead_code.DeadCodeDetector") as mock_cls:
        mock_detector = mock_cls.return_value
        mock_detector.detect.return_value = ([high_item], 5)

        params = DeadCodeInput(scope="models", confidence="high")
        result = await fn(params)
        parsed = json.loads(result)

    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["confidence"] == "high"
    assert parsed["items"][0]["dynamic_call_risk"] is False

    # medium confidence フィルタ
    with patch("rails_lens.tools.dead_code.DeadCodeDetector") as mock_cls:
        mock_detector = mock_cls.return_value
        mock_detector.detect.return_value = ([medium_item], 5)

        params = DeadCodeInput(scope="models", confidence="medium")
        result = await fn(params)
        parsed = json.loads(result)

    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["confidence"] == "medium"
    assert parsed["items"][0]["dynamic_call_risk"] is True


@pytest.mark.asyncio
async def test_dead_code_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """get_deps 例外時に ErrorResponse (INITIALIZATION_ERROR) を返す"""
    mcp = FastMCP("test")

    def get_deps_error():
        raise RuntimeError("initialization failed")

    dead_code_module.register(mcp, get_deps_error)
    fn = mcp._tool_manager._tools["rails_lens_dead_code"].fn

    params = DeadCodeInput(scope="models")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert parsed["code"] == "INITIALIZATION_ERROR"
    assert "initialization failed" in parsed["message"]

"""tools/impact_analysis.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import load_fixture
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.errors import RailsLensError, RailsRunnerExecutionError
from rails_lens.models import ImpactAnalysisInput
from rails_lens.tools import impact_analysis as impact_module


def _make_get_deps(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    def get_deps():
        return config, bridge, cache, None
    return get_deps


@pytest.fixture
def mcp_and_tool(config: RailsLensConfig, mock_bridge: RailsBridge, cache_manager: CacheManager):
    """FastMCP にツールを登録し、登録されたツール関数を返す"""
    mcp = FastMCP("test")
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    impact_module.register(mcp, get_deps)
    tool_fn = mcp._tool_manager._tools["rails_lens_analyze_impact"].fn
    return tool_fn, mock_bridge


@pytest.mark.asyncio
async def test_impact_analysis_success(mcp_and_tool) -> None:
    """正常ケース: bridge.execute と ImpactSearch 両方をモック"""
    fn, bridge = mcp_and_tool
    fixture = load_fixture("impact_analysis_user.json")
    bridge.execute = AsyncMock(return_value=fixture)

    with patch("rails_lens.tools.impact_analysis.ImpactSearch") as mock_search_cls:
        mock_search = MagicMock()
        mock_search.search.return_value = []
        mock_search_cls.return_value = mock_search

        params = ImpactAnalysisInput(model_name="User", target="email", change_type="modify")
        result = await fn(params)
        parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["target"] == "email"
    assert len(parsed["direct_impacts"]) == 2
    assert parsed["direct_impacts"][0]["category"] == "validation"
    bridge.execute.assert_called_once()


@pytest.mark.asyncio
async def test_impact_analysis_mermaid(mcp_and_tool) -> None:
    """mermaid_diagram フィールドの存在確認"""
    fn, bridge = mcp_and_tool
    fixture = load_fixture("impact_analysis_user.json")
    bridge.execute = AsyncMock(return_value=fixture)

    with patch("rails_lens.tools.impact_analysis.ImpactSearch") as mock_search_cls:
        mock_search = MagicMock()
        mock_search.search.return_value = []
        mock_search_cls.return_value = mock_search

        params = ImpactAnalysisInput(model_name="User", target="email")
        result = await fn(params)
        parsed = json.loads(result)

    assert "mermaid_diagram" in parsed
    assert "graph LR" in parsed["mermaid_diagram"]


@pytest.mark.asyncio
async def test_impact_analysis_bridge_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """bridge 例外時に ErrorResponse を返す"""
    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsLensError("bridge failed"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    impact_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_analyze_impact"].fn

    params = ImpactAnalysisInput(model_name="User", target="email")
    result = await fn(params)
    parsed = json.loads(result)

    assert "code" in parsed
    assert "message" in parsed
    assert "bridge failed" in parsed["message"]


@pytest.mark.asyncio
async def test_impact_analysis_fallback_on_runner_error(
    config: RailsLensConfig,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
    sample_rails_app: Path,
) -> None:
    """RailsRunnerExecutionError 時にファイルベースフォールバックが使われ _metadata が付与される"""
    # モデルファイルにバリデーションを追加
    models_dir = sample_rails_app / "app" / "models"
    (models_dir / "user.rb").write_text(
        "class User < ApplicationRecord\n"
        "  validates :email, presence: true\n"
        "  before_save :normalize_email\n"
        "end\n"
    )

    mcp = FastMCP("test")
    mock_bridge.execute = AsyncMock(side_effect=RailsRunnerExecutionError("unavailable"))
    get_deps = _make_get_deps(config, mock_bridge, cache_manager)
    impact_module.register(mcp, get_deps)
    fn = mcp._tool_manager._tools["rails_lens_analyze_impact"].fn

    params = ImpactAnalysisInput(model_name="User", target="email", change_type="modify")
    result = await fn(params)
    parsed = json.loads(result)

    assert "_metadata" in parsed
    assert parsed["_metadata"]["source"] == "file_analysis"
    assert parsed["model_name"] == "User"
    assert parsed["target"] == "email"
    # validationが検出されるはず
    assert any(i["category"] == "validation" for i in parsed["direct_impacts"])

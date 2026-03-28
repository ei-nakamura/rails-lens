"""tools/extract_concern_candidate.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig
from rails_lens.models import ExtractConcernInput
from rails_lens.tools import extract_concern_candidate as concern_module

_FAT_MODEL_SOURCE = """\
class User < ApplicationRecord
  def update_email
    self.email = email.strip.downcase
    self.email_verified = false
    save
  end

  def reset_email
    self.email = nil
    self.email_verified = false
    save
  end

  def confirm_email
    self.email_verified = true
    self.email_confirmed_at = Time.current
    save
  end

  def process_payment
    self.balance = balance - amount
    save
  end
end
"""

_EMPTY_MODEL_SOURCE = """\
class User < ApplicationRecord
end
"""


def _build_config(tmp_path: Path, source: str) -> RailsLensConfig:
    """tmp_path に Rails モデルファイルを作成し RailsLensConfig を返す"""
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "user.rb").write_text(source)
    return RailsLensConfig(rails_project_path=tmp_path, timeout=10)


def _make_tool(config: RailsLensConfig, bridge: RailsBridge, cache: CacheManager):
    mcp = FastMCP("test")

    def get_deps():
        return config, bridge, cache, None

    concern_module.register(mcp, get_deps)
    return mcp._tool_manager._tools["rails_lens_extract_concern_candidate"].fn


@pytest.mark.asyncio
async def test_extract_concern_rspec_model(
    tmp_path: Path,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """tmp_pathにモデルファイルを作成し、Concern候補を検出するテスト"""
    config = _build_config(tmp_path, _FAT_MODEL_SOURCE)
    fn = _make_tool(config, mock_bridge, cache_manager)

    params = ExtractConcernInput(model_name="User", min_cluster_size=2)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["total_methods"] > 0
    assert "candidates" in parsed
    assert "summary" in parsed


@pytest.mark.asyncio
async def test_extract_concern_no_candidates(
    tmp_path: Path,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """メソッドなしのモデルでは候補が空になる"""
    config = _build_config(tmp_path, _EMPTY_MODEL_SOURCE)
    fn = _make_tool(config, mock_bridge, cache_manager)

    params = ExtractConcernInput(model_name="User", min_cluster_size=3)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert parsed["total_methods"] == 0
    assert parsed["candidates"] == []


@pytest.mark.asyncio
async def test_extract_concern_min_methods(
    tmp_path: Path,
    mock_bridge: RailsBridge,
    cache_manager: CacheManager,
) -> None:
    """min_cluster_size=3 のとき、3つ以上共通カラムを持つメソッド群のみ候補になる"""
    config = _build_config(tmp_path, _FAT_MODEL_SOURCE)
    fn = _make_tool(config, mock_bridge, cache_manager)

    # min_cluster_size=3: email系3メソッドが候補になりうる
    params = ExtractConcernInput(model_name="User", min_cluster_size=3)
    result = await fn(params)
    parsed = json.loads(result)

    assert parsed["model_name"] == "User"
    assert "candidates" in parsed
    # 候補があればそれぞれ3つ以上のメソッドを含む
    for candidate in parsed["candidates"]:
        assert len(candidate["methods"]) >= 3

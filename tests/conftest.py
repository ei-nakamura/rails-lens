"""テスト共通フィクスチャ"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rails_lens.bridge.runner import RailsBridge
from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_rails_app(tmp_path: Path) -> Path:
    """テスト用のRailsプロジェクト構造を作成する"""
    project = tmp_path / "rails_app"
    project.mkdir()
    (project / "Gemfile").write_text("gem 'rails'\n")
    (project / "config").mkdir()
    (project / "app" / "models").mkdir(parents=True)
    (project / "app" / "models" / "user.rb").write_text(
        "class User < ApplicationRecord\nend\n"
    )
    (project / "db").mkdir()
    (project / "db" / "schema.rb").write_text("ActiveRecord::Schema.define {}\n")
    return project


@pytest.fixture
def config(sample_rails_app: Path) -> RailsLensConfig:
    """テスト用の設定"""
    return RailsLensConfig(
        rails_project_path=sample_rails_app,
        timeout=10,
    )


@pytest.fixture
def cache_manager(config: RailsLensConfig) -> CacheManager:
    """テスト用のキャッシュマネージャー"""
    return CacheManager(config)


@pytest.fixture
def mock_bridge(config: RailsLensConfig) -> RailsBridge:
    """subprocess をモックしたブリッジ"""
    bridge = RailsBridge(config)
    bridge.execute = AsyncMock()
    return bridge


def load_fixture(name: str) -> dict:
    """テストフィクスチャのJSONを読み込む"""
    path = FIXTURES_DIR / "ruby_output" / name
    with open(path) as f:
        return json.load(f)

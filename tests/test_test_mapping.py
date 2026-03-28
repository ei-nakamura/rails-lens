"""tools/test_mapping.py のテスト"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rails_lens.analyzers.test_mapper import TestMapper
from rails_lens.config import RailsLensConfig


def _make_config(project_path: Path) -> RailsLensConfig:
    return RailsLensConfig(rails_project_path=project_path, timeout=10)


def _make_get_deps(config: RailsLensConfig):
    def get_deps():
        return config, MagicMock(), MagicMock(), None
    return get_deps


@pytest.fixture
def rspec_project(tmp_path: Path) -> Path:
    """RSpec プロジェクト構造を作成する"""
    (tmp_path / "spec" / "models").mkdir(parents=True)
    (tmp_path / "spec" / "models" / "user_spec.rb").write_text(
        "RSpec.describe User do\n  it 'is valid' do\n  end\nend\n"
    )
    (tmp_path / "Gemfile").write_text("gem 'rails'\n")
    return tmp_path


@pytest.fixture
def minitest_project(tmp_path: Path) -> Path:
    """minitest プロジェクト構造を作成する"""
    (tmp_path / "test" / "models").mkdir(parents=True)
    (tmp_path / "test" / "models" / "user_test.rb").write_text(
        "class UserTest < ActiveSupport::TestCase\n  def test_valid\n  end\nend\n"
    )
    (tmp_path / "Gemfile").write_text("gem 'rails'\n")
    return tmp_path


def test_test_mapping_rspec(rspec_project: Path) -> None:
    """spec/ ディレクトリありの場合: RSpec として検出する"""
    config = _make_config(rspec_project)

    with patch.object(TestMapper, "_find_indirect_rspec", return_value=[]):
        mapper = TestMapper(config)
        output = mapper.map("User", include_indirect=False)

    assert output.test_framework == "rspec"
    assert len(output.direct_tests) == 1
    assert output.direct_tests[0].file == "spec/models/user_spec.rb"
    assert output.direct_tests[0].type == "unit"
    assert output.direct_tests[0].relevance == "direct"


def test_test_mapping_minitest(minitest_project: Path) -> None:
    """test/ ディレクトリありの場合: minitest として検出する"""
    config = _make_config(minitest_project)

    with patch.object(TestMapper, "_find_indirect_minitest", return_value=[]):
        mapper = TestMapper(config)
        output = mapper.map("User", include_indirect=False)

    assert output.test_framework == "minitest"
    assert len(output.direct_tests) == 1
    assert output.direct_tests[0].file == "test/models/user_test.rb"
    assert output.direct_tests[0].type == "unit"


def test_test_mapping_no_tests(tmp_path: Path) -> None:
    """テストファイルなし: unknown フレームワーク、空リスト"""
    config = _make_config(tmp_path)
    mapper = TestMapper(config)
    output = mapper.map("User", include_indirect=False)

    assert output.test_framework == "unknown"
    assert output.direct_tests == []
    assert output.indirect_tests == []


def test_run_command_format(rspec_project: Path) -> None:
    """run_command がファイルパスを含む形式で生成される"""
    config = _make_config(rspec_project)

    with patch.object(TestMapper, "_find_indirect_rspec", return_value=[]):
        mapper = TestMapper(config)
        output = mapper.map("User", include_indirect=False)

    assert "bundle exec rspec" in output.run_command
    assert "spec/models/user_spec.rb" in output.run_command

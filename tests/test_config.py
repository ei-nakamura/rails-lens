"""config.py のテスト"""
from __future__ import annotations

from pathlib import Path

import pytest

from rails_lens.config import RailsLensConfig, load_config
from rails_lens.errors import ConfigurationError


def test_default_values(sample_rails_app: Path) -> None:
    """RailsLensConfig のデフォルト値確認"""
    cfg = RailsLensConfig(rails_project_path=sample_rails_app)
    assert cfg.ruby_command == "bundle exec rails runner"
    assert cfg.timeout == 30
    assert cfg.cache_directory == ".rails-lens/cache"
    assert cfg.auto_invalidate is True
    assert cfg.search_command == "rg"
    assert "tmp" in cfg.exclude_dirs


def test_load_with_project_path(sample_rails_app: Path) -> None:
    """project_path 引数で直接設定"""
    cfg = load_config(project_path=sample_rails_app)
    assert cfg.rails_project_path == sample_rails_app.resolve()


def test_missing_project_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAILS_LENS_PROJECT_PATH 未設定で ConfigurationError"""
    monkeypatch.delenv("RAILS_LENS_PROJECT_PATH", raising=False)
    with pytest.raises(ConfigurationError):
        load_config()


def test_env_var_override(sample_rails_app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """環境変数がデフォルトを上書き"""
    monkeypatch.setenv("RAILS_LENS_PROJECT_PATH", str(sample_rails_app))
    monkeypatch.setenv("RAILS_LENS_TIMEOUT", "60")
    cfg = load_config()
    assert cfg.rails_project_path == sample_rails_app.resolve()
    assert cfg.timeout == 60


def test_infer_project_path_from_toml_location(
    sample_rails_app: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.rails-lens.toml` の親ディレクトリからプロジェクトパスを推定"""
    monkeypatch.delenv("RAILS_LENS_PROJECT_PATH", raising=False)
    toml_path = sample_rails_app / ".rails-lens.toml"
    toml_path.write_text("[rails]\ntimeout = 10\n")
    cfg = load_config(config_path=toml_path)
    assert cfg.rails_project_path == sample_rails_app.resolve()


def test_cache_path_property(sample_rails_app: Path) -> None:
    """cache_path が正しいパスを返す"""
    cfg = RailsLensConfig(rails_project_path=sample_rails_app)
    expected = sample_rails_app / ".rails-lens" / "cache"
    assert cfg.cache_path == expected

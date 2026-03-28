"""設定管理"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef] # fallback


@dataclass(frozen=True)
class RailsLensConfig:
    """rails-lens の設定値"""

    # --- Rails ---
    rails_project_path: Path
    ruby_command: str = "bundle exec rails runner"
    timeout: int = 30

    # --- Cache ---
    cache_directory: str = ".rails-lens/cache"
    auto_invalidate: bool = True

    # --- Search ---
    search_command: str = "rg"
    exclude_dirs: tuple[str, ...] = (
        "tmp", "log", "vendor", "node_modules", ".git",
    )

    @property
    def cache_path(self) -> Path:
        """キャッシュディレクトリの絶対パス"""
        return self.rails_project_path / self.cache_directory

    @property
    def ruby_scripts_path(self) -> Path:
        """Rubyスクリプトディレクトリのパス"""
        import importlib.resources as pkg_resources
        ref = pkg_resources.files("rails_lens") / ".." / ".." / "ruby"
        return Path(str(ref)).resolve()


def load_config(
    config_path: Path | None = None,
    project_path: Path | None = None,
) -> RailsLensConfig:
    """
    設定を読み込む。

    Args:
        config_path: .rails-lens.toml のパス（テスト用）
        project_path: 明示的なプロジェクトパス（テスト用）

    Returns:
        RailsLensConfig

    Raises:
        ConfigurationError: 必須設定が不足している場合
    """
    # 1. 環境変数から取得
    env_project_path = os.environ.get("RAILS_LENS_PROJECT_PATH")

    # 2. TOMLファイルから取得
    toml_data: dict[str, Any] = {}
    if config_path is None:
        # カレントディレクトリから .rails-lens.toml を探す
        candidates = [
            Path.cwd() / ".rails-lens.toml",
        ]
        if env_project_path:
            candidates.insert(0, Path(env_project_path) / ".rails-lens.toml")

        for candidate in candidates:
            if candidate.is_file():
                config_path = candidate
                break

    if config_path and config_path.is_file():
        with open(config_path, "rb") as f:
            toml_data = tomllib.load(f)

    # 3. 値の解決（環境変数 > TOML > デフォルト）
    rails_section = toml_data.get("rails", {})
    cache_section = toml_data.get("cache", {})
    search_section = toml_data.get("search", {})

    resolved_project_path = (
        project_path
        or (Path(env_project_path) if env_project_path else None)
        or (Path(rails_section["project_path"]) if "project_path" in rails_section else None)
    )

    if resolved_project_path is None:
        from rails_lens.errors import ConfigurationError
        raise ConfigurationError(
            "Rails project path is not configured. "
            "Set 'rails.project_path' in .rails-lens.toml "
            "or RAILS_LENS_PROJECT_PATH environment variable."
        )

    return RailsLensConfig(
        rails_project_path=Path(resolved_project_path).resolve(),
        ruby_command=os.environ.get(
            "RAILS_LENS_RUBY_COMMAND",
            rails_section.get("ruby_command", "bundle exec rails runner"),
        ),
        timeout=int(os.environ.get(
            "RAILS_LENS_TIMEOUT",
            rails_section.get("timeout", 30),
        )),
        cache_directory=os.environ.get(
            "RAILS_LENS_CACHE_DIR",
            cache_section.get("directory", ".rails-lens/cache"),
        ),
        auto_invalidate=cache_section.get("auto_invalidate", True),
        search_command=search_section.get("command", "rg"),
        exclude_dirs=tuple(search_section.get(
            "exclude_dirs",
            ["tmp", "log", "vendor", "node_modules", ".git"],
        )),
    )

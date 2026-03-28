"""cache/manager.py のテスト"""
from __future__ import annotations

from pathlib import Path

from rails_lens.cache.manager import CacheManager
from rails_lens.config import RailsLensConfig


def test_set_and_get(cache_manager: CacheManager) -> None:
    """書き込み→読み込み確認"""
    data = {"model_name": "User", "associations": []}
    cache_manager.set("introspect_model", "User", data)
    result = cache_manager.get("introspect_model", "User")
    assert result == data


def test_cache_miss(cache_manager: CacheManager) -> None:
    """存在しないキーで None"""
    result = cache_manager.get("introspect_model", "NonExistent")
    assert result is None


def test_sanitize_key(cache_manager: CacheManager) -> None:
    """:: と / の変換確認"""
    sanitized = CacheManager._sanitize_key("Admin::Company")
    assert "::" not in sanitized
    assert sanitized == "Admin__Company"

    sanitized2 = CacheManager._sanitize_key("some/path/key")
    assert "/" not in sanitized2
    assert sanitized2 == "some_path_key"


def test_corrupted_cache(cache_manager: CacheManager) -> None:
    """破損 JSON で None 返却"""
    # 直接破損ファイルを書く
    cache_dir = cache_manager._cache_dir / "introspect_model"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bad_file = cache_dir / "BadModel.json"
    bad_file.write_text("{ invalid json !!!!")

    result = cache_manager.get("introspect_model", "BadModel")
    assert result is None


def test_invalidate(cache_manager: CacheManager) -> None:
    """特定キャッシュ削除"""
    data = {"model_name": "Post"}
    cache_manager.set("introspect_model", "Post", data)
    assert cache_manager.get("introspect_model", "Post") is not None

    cache_manager.invalidate("introspect_model", "Post")
    assert cache_manager.get("introspect_model", "Post") is None


def test_invalidate_all(cache_manager: CacheManager) -> None:
    """全キャッシュ削除"""
    cache_manager.set("introspect_model", "User", {"model_name": "User"})
    cache_manager.set("introspect_model", "Post", {"model_name": "Post"})

    cache_manager.invalidate_all()

    assert cache_manager.get("introspect_model", "User") is None
    assert cache_manager.get("introspect_model", "Post") is None


def test_auto_invalidate_disabled(sample_rails_app: Path) -> None:
    """auto_invalidate=False で無効化しない"""
    cfg = RailsLensConfig(
        rails_project_path=sample_rails_app,
        auto_invalidate=False,
    )
    cm = CacheManager(cfg)
    data = {"model_name": "User"}
    cm.set("introspect_model", "User", data, source_files=["app/models/user.rb"])

    # ソースファイルを変更してもキャッシュは有効なまま
    user_rb = sample_rails_app / "app" / "models" / "user.rb"
    user_rb.write_text("# modified\nclass User < ApplicationRecord\nend\n")

    result = cm.get("introspect_model", "User")
    assert result == data

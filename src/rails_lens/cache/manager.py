"""JSONファイルキャッシュ管理"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rails_lens.config import RailsLensConfig

logger = logging.getLogger(__name__)


class CacheManager:
    """ファイルベースのJSONキャッシュ管理"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self._cache_dir = config.cache_path

    def get(self, tool_name: str, key: str) -> dict[str, Any] | None:
        """
        キャッシュからデータを取得する。

        Returns:
            キャッシュされたデータ（dict）。キャッシュミス or 無効の場合は None。
        """
        path = self._cache_file_path(tool_name, key)

        if not path.is_file():
            logger.debug("Cache miss: %s/%s", tool_name, key)
            return None

        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Cache read error for %s/%s: %s", tool_name, key, e)
            # 破損キャッシュは削除
            path.unlink(missing_ok=True)
            return None

        # 自動無効化チェック
        if self.config.auto_invalidate:
            metadata = cached.get("_cache_metadata", {})
            if not self._check_mtime(metadata):
                logger.info("Cache invalidated (mtime changed): %s/%s", tool_name, key)
                path.unlink(missing_ok=True)
                return None

        logger.debug("Cache hit: %s/%s", tool_name, key)
        data = cached.get("data")
        if isinstance(data, dict):
            return data
        return None

    def set(
        self,
        tool_name: str,
        key: str,
        data: dict[str, Any],
        source_files: list[str] | None = None,
    ) -> None:
        """データをキャッシュに書き込む。"""
        path = self._cache_file_path(tool_name, key)
        path.parent.mkdir(parents=True, exist_ok=True)

        # ソースファイルのmtimeを記録
        source_files_mtime: dict[str, str] = {}
        if source_files:
            for sf in source_files:
                sf_path = self.config.rails_project_path / sf
                if sf_path.is_file():
                    mtime = sf_path.stat().st_mtime
                    source_files_mtime[sf] = datetime.fromtimestamp(
                        mtime, tz=UTC
                    ).isoformat()

        cache_entry = {
            "_cache_metadata": {
                "created_at": datetime.now(tz=UTC).isoformat(),
                "source_files_mtime": source_files_mtime,
                "rails_lens_version": "0.1.0",
            },
            "data": data,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache_entry, f, ensure_ascii=False, indent=2)

        logger.debug("Cache written: %s/%s", tool_name, key)

    def invalidate(self, tool_name: str, key: str) -> None:
        """特定のキャッシュを無効化する。"""
        path = self._cache_file_path(tool_name, key)
        path.unlink(missing_ok=True)
        logger.info("Cache invalidated: %s/%s", tool_name, key)

    def invalidate_all(self) -> None:
        """全キャッシュを無効化する。"""
        if self._cache_dir.is_dir():
            shutil.rmtree(self._cache_dir)
            logger.info("All caches invalidated")

    def _cache_file_path(self, tool_name: str, key: str) -> Path:
        """キャッシュファイルのパスを生成する。"""
        sanitized_key = self._sanitize_key(key)
        return self._cache_dir / tool_name / f"{sanitized_key}.json"

    @staticmethod
    def _sanitize_key(key: str) -> str:
        """キャッシュキーをファイル名として安全な文字列に変換する。"""
        # "::" → "__", "/" → "_", その他のファイルシステム非安全文字を除去
        return key.replace("::", "__").replace("/", "_").replace("\\", "_")

    def _check_mtime(self, metadata: dict[str, Any]) -> bool:
        """
        キャッシュメタデータのmtimeと現在のファイルmtimeを比較する。

        Returns:
            True: キャッシュは有効, False: キャッシュは無効（ファイルが変更されている）
        """
        source_files_mtime = metadata.get("source_files_mtime", {})

        if not source_files_mtime:
            # ソースファイル情報がない場合は有効とみなす
            return True

        for rel_path, cached_mtime_str in source_files_mtime.items():
            file_path = self.config.rails_project_path / rel_path
            if not file_path.is_file():
                # ファイルが削除された → 無効
                return False

            current_mtime = datetime.fromtimestamp(
                file_path.stat().st_mtime, tz=UTC
            ).isoformat()

            if current_mtime != cached_mtime_str:
                return False

        return True

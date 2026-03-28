"""静的解析 — ripgrep / grep によるコード検索"""
from __future__ import annotations

import logging
import re
import subprocess

from rails_lens.config import RailsLensConfig
from rails_lens.models import MatchContext, ReferenceMatch

logger = logging.getLogger(__name__)


class GrepSearch:
    """ripgrep または grep を使ったコード検索"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self._use_ripgrep: bool | None = None  # 未検出

    def search(
        self,
        query: str,
        scope: str = "all",
        search_type: str = "any",
    ) -> list[ReferenceMatch]:
        """
        コードベースを検索してReferenceMatchのリストを返す。

        Args:
            query: 検索クエリ
            scope: 検索スコープ（models, controllers, views, services, all）
            search_type: 検索タイプ（class, method, any）

        Returns:
            ReferenceMatchのリスト
        """
        pattern = self._build_pattern(query, search_type)
        paths = self._scope_to_paths(scope)

        if self._use_ripgrep is None:
            self._use_ripgrep = self._detect_ripgrep()

        if self._use_ripgrep:
            return self._search_with_rg(pattern, paths)
        else:
            return self._search_with_grep(pattern, paths)

    def _build_pattern(self, query: str, search_type: str) -> str:
        """検索タイプに応じた正規表現パターンを生成する"""
        escaped = re.escape(query)
        if search_type == "class":
            return rf"\b{escaped}\b"
        elif search_type == "method":
            return rf"[.:]{{re.escape(query)}}|\b{escaped}\b".replace(
                "{re.escape(query)}", escaped
            )
        else:  # "any"
            return rf"\b{escaped}\b"

    def _scope_to_paths(self, scope: str) -> list[str]:
        """スコープを検索ディレクトリのリストに変換する"""
        scope_map = {
            "models": ["app/models/"],
            "controllers": ["app/controllers/"],
            "views": ["app/views/"],
            "services": ["app/services/"],
            "all": ["app/", "lib/", "config/"],
        }
        return scope_map.get(scope, scope_map["all"])

    def _detect_ripgrep(self) -> bool:
        """ripgrepが利用可能か確認する"""
        result = subprocess.run(
            ["which", "rg"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _search_with_rg(self, pattern: str, paths: list[str]) -> list[ReferenceMatch]:
        """ripgrepで検索する"""
        import json as _json
        project_root = self.config.rails_project_path
        exclude_args = []
        for d in self.config.exclude_dirs:
            exclude_args.extend(["--glob", f"!{d}"])
        cmd = ["rg", "--json", pattern] + exclude_args + [
            str(project_root / p) for p in paths
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        matches = []
        for line in result.stdout.splitlines():
            try:
                obj = _json.loads(line)
                if obj.get("type") == "match":
                    data = obj["data"]
                    file_path = data["path"]["text"]
                    line_num = data["line_number"]
                    text = data["lines"]["text"].rstrip()
                    match_type = self._classify_match(text, pattern)
                    matches.append(ReferenceMatch(
                        file=file_path, line=line_num, context=MatchContext(match=text),
                        match_type=match_type
                    ))
            except Exception:
                continue
        return matches

    def _search_with_grep(self, pattern: str, paths: list[str]) -> list[ReferenceMatch]:
        """grepで検索する（ripgrepのフォールバック）"""
        project_root = self.config.rails_project_path
        full_paths = [str(project_root / p) for p in paths]

        cmd = ["grep", "-rn", "--include=*.rb", "-E", pattern] + full_paths
        result = subprocess.run(cmd, capture_output=True, text=True)

        matches = []
        for line in result.stdout.splitlines():
            try:
                # 形式: /path/to/file.rb:42:matched line
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                file_path = parts[0]
                line_num = int(parts[1])
                text = parts[2]
                match_type = self._classify_match(text, pattern)
                matches.append(ReferenceMatch(
                    file=file_path, line=line_num, context=MatchContext(match=text),
                    match_type=match_type
                ))
            except Exception:
                continue
        return matches

    def _classify_match(self, line: str, query: str) -> str:
        """マッチした行のコンテキストからマッチタイプを推定する"""
        stripped = line.strip()

        # クラス参照パターン
        if re.search(rf'class\s+{re.escape(query)}', stripped):
            return "class_reference"
        if re.search(rf'{re.escape(query)}\.(new|find|where|create)', stripped):
            return "class_reference"
        if re.search(rf'class_name:\s*["\']?{re.escape(query)}', stripped):
            return "class_reference"

        # メソッド呼び出しパターン
        if re.search(rf'\.{re.escape(query)}[\s(]', stripped):
            return "method_call"
        if re.search(rf'def\s+{re.escape(query)}', stripped):
            return "method_call"

        # シンボル参照パターン
        if re.search(rf':{re.escape(query)}\b', stripped):
            return "symbol_reference"

        # 文字列リテラル
        if re.search(rf'["\'].*{re.escape(query)}.*["\']', stripped):
            return "string_literal"

        return "other"

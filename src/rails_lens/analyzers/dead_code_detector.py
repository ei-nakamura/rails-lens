"""静的解析: デッドコード検出（メソッド定義抽出・参照カウント）"""
from __future__ import annotations

import re

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.config import RailsLensConfig
from rails_lens.models import DeadCodeItem


class DeadCodeDetector:
    def __init__(self, config: RailsLensConfig) -> None:
        self.grep = GrepSearch(config)

    def detect(
        self,
        scope: str,
        exclude: list[str],
        model_name: str | None = None,
        confidence_filter: str = "high",
    ) -> tuple[list[DeadCodeItem], int]:
        """メソッド定義を抽出し、参照カウントから未使用を検出する。

        Returns (items, total_methods_analyzed).
        """
        items: list[DeadCodeItem] = []
        exclude_set = set(exclude)

        # スコープに応じた検索
        definitions = self.grep.search(
            r"def\s+\w+",
            scope=scope if scope != "all" else "all",
            search_type="regex",
        )

        # model_name 指定時はそのモデルのファイルのみに絞る
        if model_name:
            snake = _to_snake_case(model_name)
            definitions = [d for d in definitions if snake in d.file.replace("/", "_").lower()]

        total_analyzed = 0

        for defn in definitions:
            method_match = re.search(r"def\s+(self\.)?(\w+)", defn.context.match)
            if not method_match:
                continue

            method_type = "class_method" if method_match.group(1) else "instance_method"
            method_name = method_match.group(2)

            # Rubyの組み込みメソッド名は除外
            if method_name in _RUBY_BUILTIN_METHODS:
                continue
            if method_name in exclude_set:
                continue

            total_analyzed += 1

            # 参照カウント
            refs = self.grep.search(method_name, scope="all", search_type="any")
            # 定義自身を除外
            call_count = max(0, len(refs) - 1)

            # 動的呼び出し検出（send/public_send/method）
            dynamic = self.grep.search(
                rf'(?:send|public_send|method)\s*\(\s*[:\"]?{re.escape(method_name)}',
                scope="all",
                search_type="regex",
            )
            has_dynamic = len(dynamic) > 0

            if call_count == 0:
                confidence = "medium" if has_dynamic else "high"
                reason = (
                    "Dynamic call possible via send/public_send"
                    if has_dynamic
                    else "No references found"
                )
                item = DeadCodeItem(
                    type=method_type,
                    name=method_name,
                    file=defn.file,
                    line=defn.line,
                    confidence=confidence,
                    reason=reason,
                    reference_count=0,
                    dynamic_call_risk=has_dynamic,
                )
                if (
                    confidence_filter == "high" and confidence == "high"
                    or confidence_filter == "medium"
                ):
                    items.append(item)

        return items, total_analyzed


def _to_snake_case(name: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z])([A-Z])", r"\1_\2", s).lower()


_RUBY_BUILTIN_METHODS = frozenset({
    "initialize", "new", "to_s", "to_str", "to_i", "to_f", "to_a", "to_h",
    "inspect", "class", "object_id", "send", "respond_to?", "nil?", "freeze",
    "dup", "clone", "hash", "eql?", "equal?", "frozen?", "tap", "then",
    "itself", "yield_self",
})

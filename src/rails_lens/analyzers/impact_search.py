"""静的解析: ビュー・メーラー・ジョブ・シリアライザ内の参照検索"""
from __future__ import annotations

from rails_lens.analyzers.grep_search import GrepSearch
from rails_lens.config import RailsLensConfig
from rails_lens.models import ImpactItem


class ImpactSearch:
    """ビュー・メーラー・ジョブ・シリアライザ・コントローラでの参照を静的解析で検索する"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.grep = GrepSearch(config)

    def search(self, model_name: str, target: str, change_type: str) -> list[ImpactItem]:
        """ビュー・メーラー・ジョブ・シリアライザ・コントローラでの参照を検索"""
        items: list[ImpactItem] = []

        # (category, path_hint, severity)
        sev_remove = "breaking" if change_type == "remove" else "warning"
        search_targets = [
            ("view",       "app/views/",       sev_remove),
            ("mailer",     "app/mailers/",     sev_remove),
            ("job",        "app/jobs/",         "warning"),
            ("serializer", "app/serializers/",  "warning"),
            ("controller", "app/controllers/",  "info"),
        ]

        for category, path_hint, severity in search_targets:
            matches = self.grep.search(target, scope="all", search_type="any")
            for m in matches:
                if path_hint in m.file:
                    desc = (
                        f"{category.capitalize()} references "
                        f"'{target}' at {m.file}:{m.line}"
                    )
                    items.append(ImpactItem(
                        category=category,
                        file=m.file,
                        line=m.line,
                        description=desc,
                        severity=severity,
                        code_snippet=m.context.match if m.context else "",
                    ))

        # 重複排除 (file + line)
        seen: set[tuple[str, int]] = set()
        deduped: list[ImpactItem] = []
        for item in items:
            key = (item.file, item.line)
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        return deduped

"""FullInventoryOutput → Markdown 整形

docs/ に配置可能な品質で出力する。自動生成注記と生成日時を含む。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rails_lens.tools.screen_map import FullInventoryOutput


class InventoryFormatter:
    """FullInventoryOutput を Markdown ドキュメントに整形する。

    グルーピング対応: namespace / resource / flat（ScreenGroup の構造に従う）
    """

    def format(self, output: FullInventoryOutput) -> str:
        """FullInventoryOutput を Markdown 文字列に変換する。"""
        lines: list[str] = []

        # ---- ヘッダー ----
        lines.append("# 画面台帳（自動生成）")
        lines.append("")
        lines.append(
            "> このドキュメントは rails-lens の `full_inventory` モードにより自動生成されました。"
        )
        lines.append(f"> 生成日時: {output.generated_at}")
        lines.append(
            f"> 画面数: {output.total_screen_count}"
            f"（Web: {output.web_screen_count}, API: {output.api_endpoint_count}）"
        )
        lines.append("")

        # ---- Web 画面セクション ----
        web_groups = [
            g for g in output.groups if any(not s.is_api for s in g.screens)
        ]
        if web_groups:
            lines.append("## Web画面")
            lines.append("")
            for group in web_groups:
                web_screens = [s for s in group.screens if not s.is_api]
                if not web_screens:
                    continue
                lines.append(f"### {group.group_name}")
                lines.append(
                    "| 画面名 | URL | コントローラ | テンプレート | パーシャル数 | モデル |"
                )
                lines.append("|---|---|---|---|---|---|")
                for screen in web_screens:
                    url = f"{screen.http_method} {screen.url_pattern}"
                    template = screen.template or "—"
                    models = ", ".join(screen.models) if screen.models else "—"
                    lines.append(
                        f"| {screen.screen_name} | {url} | {screen.controller_action}"
                        f" | {template} | {screen.partial_count} | {models} |"
                    )
                lines.append("")

        # ---- API エンドポイントセクション ----
        api_screens_all = [
            s for g in output.groups for s in g.screens if s.is_api
        ]
        if api_screens_all:
            lines.append("## APIエンドポイント")
            lines.append(
                "| エンドポイント名 | URL | コントローラ | シリアライザ | モデル |"
            )
            lines.append("|---|---|---|---|---|")
            for screen in api_screens_all:
                url = f"{screen.http_method} {screen.url_pattern}"
                serializer = screen.serializer or "—"
                models = ", ".join(screen.models) if screen.models else "—"
                lines.append(
                    f"| {screen.screen_name} | {url} | {screen.controller_action}"
                    f" | {serializer} | {models} |"
                )
            lines.append("")

        # ---- 共有パーシャルセクション ----
        if output.shared_partials:
            lines.append("## 共有パーシャル使用状況")
            lines.append("| パーシャル | 使用画面数 | 影響レベル |")
            lines.append("|---|---|---|")
            for partial in output.shared_partials:
                lines.append(
                    f"| {partial.file} | {partial.screen_count} | {partial.impact_level} |"
                )
            lines.append("")

        return "\n".join(lines)

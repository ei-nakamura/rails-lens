"""rails_lens_dead_code ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.analyzers.dead_code_detector import DeadCodeDetector
from rails_lens.models import (
    DeadCodeInput,
    DeadCodeOutput,
    ErrorResponse,
)


async def dead_code_impl(
    params: DeadCodeInput,
    bridge: Any,
    config: Any,
) -> DeadCodeOutput:
    """MCPデコレータなしで同じロジックを実行し、DeadCodeOutput を返す"""
    excluded: list[str] = []
    if params.model_name:
        try:
            raw = await bridge.execute(
                "dead_code_check.rb",
                args=[params.model_name, "false"],
            )
            excluded = raw.get("excluded_methods", [])
        except Exception:
            pass

    detector = DeadCodeDetector(config)
    items, total_analyzed = detector.detect(
        scope=params.scope,
        exclude=excluded,
        model_name=params.model_name,
        confidence_filter=params.confidence,
    )
    return DeadCodeOutput(
        scope=params.scope,
        model_name=params.model_name,
        items=items,
        total_methods_analyzed=total_analyzed,
        total_dead_code_found=len(items),
        summary=(
            f"{len(items)} dead code item(s) found "
            f"(confidence: {params.confidence}) "
            f"out of {total_analyzed} method(s) analyzed"
        ),
    )


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_dead_code",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def detect_dead_code(params: DeadCodeInput) -> str:
        """未使用のメソッド・コールバック・スコープを検出し、削除の安全性を
        confidence 付きで報告する"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        excluded: list[str] = []

        # ランタイム補完: Ruby スクリプトで除外リストを取得
        if params.model_name:
            try:
                raw = await bridge.execute(
                    "dead_code_check.rb",
                    args=[params.model_name, "false"],
                )
                excluded = raw.get("excluded_methods", [])
            except Exception:
                # ランタイム失敗時は静的解析のみで継続
                pass

        # 静的解析
        try:
            detector = DeadCodeDetector(config)
            items, total_analyzed = detector.detect(
                scope=params.scope,
                exclude=excluded,
                model_name=params.model_name,
                confidence_filter=params.confidence,
            )
        except Exception as e:
            return ErrorResponse(
                code="STATIC_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        output = DeadCodeOutput(
            scope=params.scope,
            model_name=params.model_name,
            items=items,
            total_methods_analyzed=total_analyzed,
            total_dead_code_found=len(items),
            summary=(
                f"{len(items)} dead code item(s) found "
                f"(confidence: {params.confidence}) "
                f"out of {total_analyzed} method(s) analyzed"
            ),
        )
        return json.dumps(output.model_dump(), indent=2, ensure_ascii=False)

"""rails_lens_extract_concern_candidate ツール"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.analyzers.concern_extractor import ConcernExtractor
from rails_lens.models import (
    ErrorResponse,
    ExtractConcernInput,
    ExtractConcernOutput,
)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_extract_concern_candidate",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def extract_concern_candidate(params: ExtractConcernInput) -> str:
        """Fat Model のメソッドを凝集度で分析し、Concern切り出し候補を根拠付きで提示する"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        # ランタイム補完: 既存Concernリストを introspect_model キャッシュから取得
        existing_concerns: list[str] = []
        try:
            cached = cache.get(params.model_name)
            if cached:
                existing_concerns = [
                    m for m in cached.get("included_modules", [])
                    if "Concern" in m or "concern" in m.lower()
                ]
        except Exception:
            pass

        # 静的解析
        try:
            extractor = ConcernExtractor(config)
            candidates, total_methods, total_lines, unclustered = extractor.extract(
                model_name=params.model_name,
                min_cluster_size=params.min_cluster_size,
                existing_concerns=existing_concerns,
            )
        except Exception as e:
            return ErrorResponse(
                code="STATIC_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        output = ExtractConcernOutput(
            model_name=params.model_name,
            total_methods=total_methods,
            total_lines=total_lines,
            candidates=candidates,
            unclustered_methods=unclustered,
            summary=(
                f"{len(candidates)} Concern candidate(s) identified "
                f"from {total_methods} method(s) in {params.model_name} "
                f"({total_lines} lines)"
            ),
        )
        return json.dumps(output.model_dump(), indent=2, ensure_ascii=False)

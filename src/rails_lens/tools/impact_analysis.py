"""rails_lens_analyze_impact ツール"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.analyzers.impact_search import ImpactSearch
from rails_lens.models import (
    ErrorResponse,
    ImpactAnalysisInput,
    ImpactAnalysisOutput,
    ImpactItem,
)


def _generate_mermaid_diagram(output: ImpactAnalysisOutput) -> str:
    """影響範囲を Mermaid graph LR 形式で生成する"""
    lines = ["graph LR"]
    lines.append(f'  TARGET["{output.model_name}.{output.target}"]')

    category_icons = {
        "callback": "CB",
        "validation": "VL",
        "scope": "SC",
        "view": "VW",
        "mailer": "ML",
        "job": "JB",
        "serializer": "SR",
        "controller": "CT",
        "association_cascade": "AC",
    }

    for i, impact in enumerate(output.direct_impacts):
        node_id = f"I{i}"
        icon = category_icons.get(impact.category, "??")
        label = f"{icon}: {impact.description[:40]}"
        lines.append(f'  {node_id}["{label}"]')
        style = "fill:#f88" if impact.severity == "breaking" else (
            "fill:#fa4" if impact.severity == "warning" else "fill:#8f8"
        )
        lines.append(f"  style {node_id} {style}")
        lines.append(f"  TARGET --> {node_id}")

    for i, cascade in enumerate(output.cascade_effects):
        node_id = f"C{i}"
        label = f"CASCADE: {cascade.target_model} ({cascade.relation})"
        lines.append(f'  {node_id}["{label}"]')
        lines.append(f"  style {node_id} fill:#f88")
        lines.append(f"  TARGET --> {node_id}")

    return "\n".join(lines)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_analyze_impact",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def analyze_impact(params: ImpactAnalysisInput) -> str:
        """カラムやメソッドを変更した場合の影響範囲（コールバック・バリデーション・ビュー・メーラー等）を分析する"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            # ランタイム解析 (Ruby)
            raw_data = await bridge.execute(
                "impact_analysis.rb",
                args=[params.model_name, params.target, params.change_type],
            )
            output = ImpactAnalysisOutput(**raw_data)
        except Exception as e:
            return ErrorResponse(
                code="RUNTIME_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            # 静的解析 (Python) でビュー・メーラー・ジョブ等を検索してマージ
            static_items: list[ImpactItem] = ImpactSearch(config).search(
                params.model_name, params.target, params.change_type
            )
            # 重複排除して追加（file+line でユニーク）
            existing_keys = {(i.file, i.line) for i in output.direct_impacts}
            for item in static_items:
                if (item.file, item.line) not in existing_keys:
                    output.direct_impacts.append(item)
                    existing_keys.add((item.file, item.line))
        except Exception:
            # 静的解析は失敗してもランタイム結果を返す
            pass

        # affected_files を再構築
        output.affected_files = sorted(
            {i.file for i in output.direct_impacts if i.file}
        )

        # Mermaid ダイアグラム生成
        output.summary = (
            f"{len(output.direct_impacts)} direct impact(s) and "
            f"{len(output.cascade_effects)} cascade effect(s) found for "
            f"'{params.target}' ({params.change_type})"
        )

        result = output.model_dump()
        result["mermaid_diagram"] = _generate_mermaid_diagram(output)

        import json
        return json.dumps(result, indent=2, ensure_ascii=False)

"""rails_lens_analyze_impact ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.analyzers.impact_search import ImpactSearch
from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import (
    ErrorResponse,
    ImpactAnalysisInput,
    ImpactAnalysisOutput,
    ImpactItem,
)

_CALLBACK_RE = re.compile(
    r'\b(before_\w+|after_\w+|around_\w+)\s+[:\s]*(\w+)'
)
_VALIDATION_RE = re.compile(r'\bvalidates\s+:(\w+)')
_ASSOC_RE = re.compile(
    r'\b(has_many|belongs_to|has_one|has_and_belongs_to_many)\s+:(\w+)'
    r'(?:[^\n]*?dependent:\s*:(\w+))?'
)
_CLASS_RE = re.compile(r'\bclass\s+(\w+)')


def _fallback_impact_analysis(config: Any, params: ImpactAnalysisInput) -> dict[str, Any]:
    """ImpactSearch(grep) + モデルファイル解析で影響範囲を推定するファイルベースフォールバック"""
    direct_impacts: list[dict[str, Any]] = []
    cascade_effects: list[dict[str, Any]] = []

    # grep-based static search (views/mailers/jobs/serializers/controllers)
    try:
        static_items: list[ImpactItem] = ImpactSearch(config).search(
            params.model_name, params.target, params.change_type
        )
        for item in static_items:
            direct_impacts.append(item.model_dump())
    except Exception:
        pass

    # model files: callbacks/validations referencing target + cascade effects
    models_dir = Path(config.rails_project_path) / "app" / "models"
    if models_dir.exists():
        sev_remove = "breaking" if params.change_type == "remove" else "warning"
        for model_file in sorted(models_dir.rglob("*.rb")):
            try:
                content = model_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m = _CLASS_RE.search(content)
            if not m:
                continue
            file_model = m.group(1)
            rel_path = str(model_file)

            for line_no, line in enumerate(content.splitlines(), 1):
                # callbacks that reference target
                cb_m = _CALLBACK_RE.search(line)
                if cb_m and params.target in line:
                    direct_impacts.append({
                        "category": "callback",
                        "file": rel_path,
                        "line": line_no,
                        "description": (
                            f"Callback '{cb_m.group(1)}' references "
                            f"'{params.target}' in {file_model}"
                        ),
                        "severity": sev_remove,
                        "code_snippet": line.strip(),
                    })

                # validations on target (only in the target model)
                if file_model == params.model_name:
                    val_m = _VALIDATION_RE.search(line)
                    if val_m and val_m.group(1) == params.target:
                        direct_impacts.append({
                            "category": "validation",
                            "file": rel_path,
                            "line": line_no,
                            "description": (
                                f"Validation on '{params.target}' in {file_model}"
                            ),
                            "severity": sev_remove,
                            "code_snippet": line.strip(),
                        })

            # cascade effects: dependent associations pointing at model_name
            model_lower = params.model_name.lower()
            for am in _ASSOC_RE.finditer(content):
                relation_type = am.group(1)
                assoc_name = am.group(2)
                dependent = am.group(3)
                if not dependent:
                    continue
                singular = assoc_name
                if relation_type in ("has_many", "has_and_belongs_to_many"):
                    if assoc_name.endswith("ies"):
                        singular = assoc_name[:-3] + "y"
                    elif assoc_name.endswith("s"):
                        singular = assoc_name[:-1]
                if singular.lower() == model_lower:
                    cascade_effects.append({
                        "source_model": file_model,
                        "target_model": params.model_name,
                        "relation": f"dependent_{dependent}",
                        "description": (
                            f"{file_model}.{relation_type} :{assoc_name} "
                            f"dependent: :{dependent}"
                        ),
                    })

    # dedup by (file, line)
    seen: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for di in direct_impacts:
        key = (di["file"], di["line"])
        if key not in seen:
            seen.add(key)
            deduped.append(di)

    affected_files = sorted({di["file"] for di in deduped if di.get("file")})
    return {
        "model_name": params.model_name,
        "target": params.target,
        "change_type": params.change_type,
        "target_type": "column",
        "direct_impacts": deduped,
        "cascade_effects": cascade_effects,
        "affected_files": affected_files,
        "summary": (
            f"{len(deduped)} direct impact(s) and "
            f"{len(cascade_effects)} cascade effect(s) found for "
            f"'{params.target}' (file analysis)"
        ),
        "mermaid_diagram": "",
    }


async def impact_analysis_impl(
    params: ImpactAnalysisInput,
    bridge: Any,
    config: Any,
) -> ImpactAnalysisOutput:
    """MCPデコレータなしで同じロジックを実行し、ImpactAnalysisOutput を返す"""
    is_fallback = False
    try:
        raw_data = await bridge.execute(
            "impact_analysis.rb",
            args=[params.model_name, params.target, params.change_type],
        )
        output = ImpactAnalysisOutput(**raw_data)
    except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
        if config is not None:
            raw_data = _fallback_impact_analysis(config, params)
            raw_data["_metadata"] = {"source": "file_analysis", "note": "Rails runner unavailable"}
            output = ImpactAnalysisOutput(
                **{k: v for k, v in raw_data.items() if k != "_metadata"}
            )
            is_fallback = True
        else:
            raise

    if not is_fallback:
        try:
            static_items: list[ImpactItem] = ImpactSearch(config).search(
                params.model_name, params.target, params.change_type
            )
            existing_keys = {(i.file, i.line) for i in output.direct_impacts}
            for item in static_items:
                if (item.file, item.line) not in existing_keys:
                    output.direct_impacts.append(item)
                    existing_keys.add((item.file, item.line))
        except Exception:
            pass

    output.affected_files = sorted(
        {i.file for i in output.direct_impacts if i.file}
    )
    output.summary = (
        f"{len(output.direct_impacts)} direct impact(s) and "
        f"{len(output.cascade_effects)} cascade effect(s) found for "
        f"'{params.target}' ({params.change_type})"
    )
    output.mermaid_diagram = _generate_mermaid_diagram(output)
    return output


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

        is_fallback = False
        try:
            raw_data = await bridge.execute(
                "impact_analysis.rb",
                args=[params.model_name, params.target, params.change_type],
            )
            output = ImpactAnalysisOutput(**raw_data)
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            raw_data = _fallback_impact_analysis(config, params)
            raw_data["_metadata"] = {"source": "file_analysis", "note": "Rails runner unavailable"}
            output = ImpactAnalysisOutput(
                **{k: v for k, v in raw_data.items() if k != "_metadata"}
            )
            is_fallback = True
        except Exception as e:
            return ErrorResponse(
                code="RUNTIME_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        if not is_fallback:
            try:
                static_items: list[ImpactItem] = ImpactSearch(config).search(
                    params.model_name, params.target, params.change_type
                )
                existing_keys = {(i.file, i.line) for i in output.direct_impacts}
                for item in static_items:
                    if (item.file, item.line) not in existing_keys:
                        output.direct_impacts.append(item)
                        existing_keys.add((item.file, item.line))
            except Exception:
                pass

        output.affected_files = sorted(
            {i.file for i in output.direct_impacts if i.file}
        )
        output.summary = (
            f"{len(output.direct_impacts)} direct impact(s) and "
            f"{len(output.cascade_effects)} cascade effect(s) found for "
            f"'{params.target}' ({params.change_type})"
        )

        result = output.model_dump()
        result["mermaid_diagram"] = _generate_mermaid_diagram(output)

        if is_fallback:
            result["_metadata"] = raw_data.get("_metadata")

        return json.dumps(result, indent=2, ensure_ascii=False)

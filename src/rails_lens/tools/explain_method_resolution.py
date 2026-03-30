"""rails_lens_explain_method_resolution ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse, MethodResolutionInput, MethodResolutionOutput


def _model_name_to_rel_path(model_name: str) -> str:
    """'Admin::User' -> 'admin/user' for file lookup"""
    parts = model_name.split("::")
    snake_parts = []
    for part in parts:
        s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', part)
        snake_parts.append(re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1).lower())
    return "/".join(snake_parts)


def _fallback_method_resolution(config: Any, params: MethodResolutionInput) -> dict[str, Any]:
    """Rails runner不使用時: include/extend/prepend行を抽出→擬似ancestorチェーン構築"""
    model_rel = _model_name_to_rel_path(params.model_name)
    model_path = config.rails_project_path / "app" / "models" / f"{model_rel}.rb"

    prepended: list[dict[str, Any]] = []
    included: list[dict[str, Any]] = []
    extended: list[dict[str, Any]] = []

    if model_path.exists():
        try:
            content = model_path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'^\s*prepend\s+([\w:]+)', content, re.MULTILINE):
                prepended.append({"name": m.group(1), "type": "concern", "source_file": None})
            for m in re.finditer(r'^\s*include\s+([\w:]+)', content, re.MULTILINE):
                included.append({"name": m.group(1), "type": "concern", "source_file": None})
            for m in re.finditer(r'^\s*extend\s+([\w:]+)', content, re.MULTILINE):
                extended.append({"name": m.group(1), "type": "concern", "source_file": None})
        except OSError:
            pass

    self_entry: dict[str, Any] = {
        "name": params.model_name,
        "type": "self",
        "source_file": str(model_path) if model_path.exists() else None,
    }
    # Ruby MRO: prepended modules appear before self; included modules after self
    ancestors = (
        prepended
        + [self_entry]
        + included
        + extended
        + [
            {"name": "ApplicationRecord", "type": "active_record_internal", "source_file": None},
            {"name": "ActiveRecord::Base", "type": "active_record_internal", "source_file": None},
        ]
    )

    return {
        "model_name": params.model_name,
        "ancestors": ancestors,
        "method_owner": None,
        "super_chain": [],
        "monkey_patches": [],
        "_metadata": {
            "source": "file_analysis",
            "note": (
                "Full MRO requires Rails runner. "
                "include/extend/prepend extracted from source file."
            ),
        },
    }


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_explain_method_resolution",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def explain_method_resolution(params: MethodResolutionInput) -> str:
        """モデルのメソッド解決順序（MRO）・祖先チェーン・メソッドオーナーを返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            raw_data = await bridge.execute(
                "method_resolution.rb",
                args=[
                    params.model_name,
                    params.method_name or "",
                    str(params.show_internal).lower(),
                ],
            )
            output = MethodResolutionOutput(**raw_data)
            return output.model_dump_json(indent=2)
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            result = _fallback_method_resolution(config, params)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(
                code="METHOD_RESOLUTION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

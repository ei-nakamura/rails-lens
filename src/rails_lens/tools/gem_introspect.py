"""rails_lens_gem_introspect ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse, GemIntrospectInput, GemIntrospectOutput


def _fallback_gem_introspect(config: Any, params: GemIntrospectInput) -> dict[str, Any]:
    """Rails runner不使用時: Gemfile/Gemfile.lockパース"""
    project_path = config.rails_project_path
    gemfile_path = project_path / "Gemfile"
    lockfile_path = project_path / "Gemfile.lock"

    gems: list[dict[str, Any]] = []

    if gemfile_path.exists():
        try:
            content = gemfile_path.read_text(encoding="utf-8", errors="replace")
            current_group: str | None = None

            for line in content.splitlines():
                group_m = re.match(r'\s*group\s+(.+?)\s+do\b', line)
                if group_m:
                    sym_m = re.search(r':(\w+)', group_m.group(1))
                    current_group = sym_m.group(1) if sym_m else None
                    continue
                if re.match(r'\s*end\b', line):
                    current_group = None
                    continue

                gem_m = re.match(
                    r"""\s*gem\s+["']([\w][\w\-]*)["'](?:\s*,\s*["']([^"']+)["'])?""",
                    line,
                )
                if gem_m:
                    gem_name = gem_m.group(1)
                    version_constraint = gem_m.group(2)
                    if params.gem_name and gem_name != params.gem_name:
                        continue
                    gems.append({
                        "name": gem_name,
                        "version_constraint": version_constraint,
                        "group": current_group,
                    })
        except OSError:
            pass

    locked_versions: dict[str, str] = {}
    if lockfile_path.exists():
        try:
            lock_content = lockfile_path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'^    ([\w][\w\-]*)\s+\(([^)]+)\)', lock_content, re.MULTILINE):
                locked_versions[m.group(1)] = m.group(2)
        except OSError:
            pass

    gem_methods = []
    for gem in gems:
        version = locked_versions.get(gem["name"], gem["version_constraint"] or "unknown")
        gem_methods.append({
            "gem_name": gem["name"],
            "method_name": f"(version: {version})",
            "source_file": None,
        })

    return {
        "model_name": params.model_name,
        "gem_methods": gem_methods,
        "gem_callbacks": [],
        "gem_routes": [],
        "_metadata": {
            "source": "file_analysis",
            "note": (
                "Method injection details require Rails runner. "
                "Gemfile/Gemfile.lock parsed for gem list."
            ),
            "gems_found": len(gems),
            "lockfile_available": lockfile_path.exists(),
        },
    }


async def gem_introspect_impl(
    params: GemIntrospectInput,
    bridge: Any,
) -> GemIntrospectOutput:
    """MCPデコレータなしで同じロジックを実行し、GemIntrospectOutput を返す"""
    raw_data = await bridge.execute(
        "gem_introspect.rb",
        args=[params.model_name, params.gem_name or ""],
    )
    return GemIntrospectOutput(**raw_data)


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_gem_introspect",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def gem_introspect(params: GemIntrospectInput) -> str:
        """モデルに影響を与えているGemのメソッド・コールバック・ルートを返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            raw_data = await bridge.execute(
                "gem_introspect.rb",
                args=[params.model_name, params.gem_name or ""],
            )
            output = GemIntrospectOutput(**raw_data)
            return output.model_dump_json(indent=2)
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            result = _fallback_gem_introspect(config, params)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(
                code="GEM_INTROSPECT_ERROR", message=str(e)
            ).model_dump_json(indent=2)

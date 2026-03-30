"""rails_lens_get_routes ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import ErrorResponse

_RESOURCES_RE = re.compile(r'^\s*resources?\s+:(\w+)', re.MULTILINE)
_HTTP_VERB_RE = re.compile(
    r'^\s*(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"](?:.*?to:\s*[\'"](\w+#\w+)[\'"])?',
    re.MULTILINE,
)
_NAMESPACE_RE = re.compile(r'^\s*namespace\s+:(\w+)', re.MULTILINE)
_SCOPE_RE = re.compile(r'^\s*scope\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE)

_RESOURCES_ACTIONS = [
    ("GET", "/{name}", "{ctrl}#index"),
    ("POST", "/{name}", "{ctrl}#create"),
    ("GET", "/{name}/new", "{ctrl}#new"),
    ("GET", "/{name}/:id/edit", "{ctrl}#edit"),
    ("GET", "/{name}/:id", "{ctrl}#show"),
    ("PATCH", "/{name}/:id", "{ctrl}#update"),
    ("PUT", "/{name}/:id", "{ctrl}#update"),
    ("DELETE", "/{name}/:id", "{ctrl}#destroy"),
]


def _fallback_get_routes(config: Any) -> dict[str, Any]:
    """config/routes.rb をテキストパースしてルーティング情報を返す"""
    _meta = {"source": "file_analysis", "note": "Rails runner unavailable"}
    routes_path = config.rails_project_path / "config" / "routes.rb"
    if not routes_path.exists():
        return {"routes": [], "_metadata": _meta}

    try:
        content = routes_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"routes": [], "_metadata": _meta}

    routes = []

    # resources/resource
    for m in _RESOURCES_RE.finditer(content):
        resource = m.group(1)
        ctrl = resource
        for verb, path_tmpl, action_tmpl in _RESOURCES_ACTIONS:
            routes.append({
                "verb": verb,
                "path": path_tmpl.format(name=resource),
                "action": action_tmpl.format(ctrl=ctrl),
                "source": "resources",
            })

    # explicit HTTP verbs
    for m in _HTTP_VERB_RE.finditer(content):
        verb = m.group(1).upper()
        path = m.group(2)
        action = m.group(3) or ""
        routes.append({"verb": verb, "path": path, "action": action, "source": "explicit"})

    return {"routes": routes, "_metadata": _meta}


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_get_routes",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_routes() -> str:
        """RailsアプリのルーティングをすべてJSON形式で返す"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)
        try:
            cached = cache.get("get_routes", "routes")
            if cached:
                return json.dumps(cached, ensure_ascii=False, indent=2)
            try:
                raw_data = await bridge.execute("dump_routes.rb", args=[])
            except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
                raw_data = _fallback_get_routes(config)
            cache.set("get_routes", "routes", raw_data, source_files=[])
            return json.dumps(raw_data, ensure_ascii=False, indent=2)
        except Exception as e:
            return ErrorResponse(code="GET_ROUTES_ERROR", message=str(e)).model_dump_json(indent=2)

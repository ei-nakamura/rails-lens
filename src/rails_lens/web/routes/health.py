"""プロジェクト健全性 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import CircularDependenciesInput, DeadCodeInput
from rails_lens.tools.circular_dependencies import circular_dependencies_impl
from rails_lens.tools.dead_code import dead_code_impl

router = APIRouter()


@router.get("/health", response_class=HTMLResponse)
async def project_health(request: Request) -> HTMLResponse:
    bridge = request.app.state.bridge
    config = request.app.state.config
    templates = request.app.state.templates

    circular_error: str | None = None
    try:
        circular = await circular_dependencies_impl(
            CircularDependenciesInput(entry_point=None, format="mermaid"),
            bridge,
        )
    except Exception as exc:
        circular = None
        circular_error = str(exc)

    dead_code_error: str | None = None
    try:
        dead_code = await dead_code_impl(
            DeadCodeInput(scope="models", model_name=None, confidence="high"),
            bridge,
            config,
        )
    except Exception as exc:
        dead_code = None
        dead_code_error = str(exc)

    return templates.TemplateResponse(request, "health.html", {  # type: ignore[no-any-return]
        "circular": circular.model_dump() if circular else None,
        "dead_code": dead_code.model_dump() if dead_code else None,
        "has_cycles": (circular.total_cycles > 0) if circular else False,
        "circular_error": circular_error,
        "dead_code_error": dead_code_error,
    })

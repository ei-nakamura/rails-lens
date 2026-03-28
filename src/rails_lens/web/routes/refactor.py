"""リファクタリング支援 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import DeadCodeInput, ExtractConcernInput
from rails_lens.tools.dead_code import dead_code_impl
from rails_lens.tools.extract_concern_candidate import extract_concern_impl

router = APIRouter()


@router.get("/refactor/{model_name}", response_class=HTMLResponse)
async def refactor_support(request: Request, model_name: str) -> HTMLResponse:
    cache = request.app.state.cache
    config = request.app.state.config
    bridge = request.app.state.bridge
    templates = request.app.state.templates

    try:
        concern_output = await extract_concern_impl(
            ExtractConcernInput(model_name=model_name, min_cluster_size=3),
            cache,
            config,
        )
        candidates = concern_output.model_dump()
        concern_error = None
    except Exception as exc:
        candidates = None
        concern_error = str(exc)

    try:
        dead_code_output = await dead_code_impl(
            DeadCodeInput(scope="models", model_name=model_name, confidence="high"),
            bridge,
            config,
        )
        dead_code = dead_code_output.model_dump()
        dead_code_error = None
    except Exception as exc:
        dead_code = None
        dead_code_error = str(exc)

    return templates.TemplateResponse(request, "refactor.html", {  # type: ignore[no-any-return]
        "model_name": model_name,
        "candidates": candidates,
        "dead_code": dead_code,
        "concern_error": concern_error,
        "dead_code_error": dead_code_error,
    })

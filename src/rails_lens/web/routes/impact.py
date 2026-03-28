"""変更影響分析 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import ImpactAnalysisInput
from rails_lens.tools.impact_analysis import impact_analysis_impl

router = APIRouter()


@router.get("/impact/{model_name}", response_class=HTMLResponse)
async def impact_analysis(
    request: Request,
    model_name: str,
    target: str | None = None,
    change_type: str = "modify",
) -> HTMLResponse:
    bridge = request.app.state.bridge
    config = request.app.state.config
    templates = request.app.state.templates

    result = None
    result_error = None
    if target:
        try:
            output = await impact_analysis_impl(
                ImpactAnalysisInput(
                    model_name=model_name,
                    target=target,
                    change_type=change_type,
                ),
                bridge,
                config,
            )
            result = output.model_dump()
        except Exception as exc:
            result_error = str(exc)

    return templates.TemplateResponse(request, "impact.html", {  # type: ignore[no-any-return]
        "model_name": model_name,
        "target": target,
        "change_type": change_type,
        "result": result,
        "result_error": result_error,
    })

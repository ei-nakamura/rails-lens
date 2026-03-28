"""リクエストフロー ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import DataFlowInput
from rails_lens.tools.data_flow import data_flow_impl

router = APIRouter()


@router.get("/flow", response_class=HTMLResponse)
async def flow_selector(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "flow_selector.html", {})  # type: ignore[no-any-return]


@router.get("/flow/{controller}/{action}", response_class=HTMLResponse)
async def request_flow(request: Request, controller: str, action: str) -> HTMLResponse:
    bridge = request.app.state.bridge
    grep = request.app.state.grep if hasattr(request.app.state, "grep") else None
    templates = request.app.state.templates

    try:
        flow = await data_flow_impl(
            DataFlowInput(
                controller_action=f"{controller}#{action}",
                model_name=None,
                attribute=None,
            ),
            bridge,
            grep,
        )
        flow_data = flow.model_dump()
        flow_error = None
    except Exception as exc:
        flow_data = None
        flow_error = str(exc)

    return templates.TemplateResponse(request, "flow.html", {  # type: ignore[no-any-return]
        "controller": controller,
        "action": action,
        "flow": flow_data,
        "mermaid_code": flow_data["mermaid_diagram"] if flow_data else "",
        "flow_error": flow_error,
    })

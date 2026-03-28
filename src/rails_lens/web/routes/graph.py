"""依存関係グラフ ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import DependencyGraphInput
from rails_lens.tools.dependency_graph import dependency_graph_impl

router = APIRouter()


@router.get("/graph/{model_name}", response_class=HTMLResponse)
async def dependency_graph(
    request: Request,
    model_name: str,
    depth: int = 2,
) -> HTMLResponse:
    bridge = request.app.state.bridge
    templates = request.app.state.templates

    graph = await dependency_graph_impl(
        DependencyGraphInput(entry_point=model_name, depth=depth, format="mermaid"),
        bridge,
    )

    return templates.TemplateResponse(request, "graph.html", {  # type: ignore[no-any-return]
        "model_name": model_name,
        "depth": depth,
        "mermaid_code": graph.mermaid_diagram,
    })

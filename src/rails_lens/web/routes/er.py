"""ER図 ルート"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import IntrospectModelInput
from rails_lens.tools.introspect_model import introspect_model_impl
from rails_lens.tools.list_models import list_models_impl
from rails_lens.web.er_builder import generate_er_diagram

router = APIRouter()


@router.get("/er", response_class=HTMLResponse)
async def er_diagram(request: Request, focus: str | None = None) -> HTMLResponse:
    bridge = request.app.state.bridge
    cache = request.app.state.cache
    templates = request.app.state.templates

    models_output = await list_models_impl(bridge)

    all_models = []
    for m in models_output.models:
        model_data = await introspect_model_impl(
            IntrospectModelInput(model_name=m.name, sections=["associations", "columns"]),
            bridge,
            cache,
        )
        all_models.append(model_data)

    if focus:
        all_models = _filter_by_focus(all_models, focus)

    mermaid_code = generate_er_diagram(all_models)

    return templates.TemplateResponse(request, "er.html", {  # type: ignore[no-any-return]
        "mermaid_code": mermaid_code,
        "focus": focus,
    })


def _filter_by_focus(models: list[dict[str, Any]], focus: str) -> list[dict[str, Any]]:
    """focusモデルとその直接関連モデルのみ返す"""
    related: set[str] = {focus}
    for m in models:
        if m.get("model_name") == focus:
            for assoc in m.get("associations", []):
                target = assoc.get("klass", "") or assoc.get("class_name", "")
                if target:
                    related.add(target)
    return [m for m in models if m.get("model_name") in related]

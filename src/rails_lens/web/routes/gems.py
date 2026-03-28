"""Gem情報 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import GemIntrospectInput
from rails_lens.tools.gem_introspect import gem_introspect_impl

router = APIRouter()


@router.get("/gems", response_class=HTMLResponse)
async def gems_list(request: Request) -> HTMLResponse:
    bridge = request.app.state.bridge
    templates = request.app.state.templates

    try:
        result = await gem_introspect_impl(
            GemIntrospectInput(model_name=""),
            bridge,
        )
        # gem名でユニーク一覧を作成
        gem_names: set[str] = set()
        for m in result.gem_methods:
            gem_names.add(m.gem_name)
        for c in result.gem_callbacks:
            gem_names.add(c.gem_name)
        for r in result.gem_routes:
            gem_names.add(r.gem_name)
        gems_data = result.model_dump()
        gems_data["gem_names"] = sorted(gem_names)
        gems_error = None
    except Exception as exc:
        gems_data = None
        gems_error = str(exc)

    return templates.TemplateResponse(request, "gems.html", {  # type: ignore[no-any-return]
        "gems": gems_data,
        "gems_error": gems_error,
    })


@router.get("/gems/{gem_name}", response_class=HTMLResponse)
async def gem_detail(request: Request, gem_name: str) -> HTMLResponse:
    bridge = request.app.state.bridge
    templates = request.app.state.templates

    try:
        result = await gem_introspect_impl(
            GemIntrospectInput(model_name="", gem_name=gem_name),
            bridge,
        )
        gem_data = result.model_dump()
        gem_error = None
    except Exception as exc:
        gem_data = None
        gem_error = str(exc)

    return templates.TemplateResponse(request, "gem_detail.html", {  # type: ignore[no-any-return]
        "gem_name": gem_name,
        "gem": gem_data,
        "gem_error": gem_error,
    })

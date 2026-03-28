"""キャッシュ管理 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from rails_lens.tools.refresh_cache import refresh_cache_impl

router = APIRouter()


@router.get("/cache", response_class=HTMLResponse)
async def cache_management(request: Request) -> HTMLResponse:
    cache = request.app.state.cache
    templates = request.app.state.templates

    entries = cache.list_entries() if hasattr(cache, "list_entries") else []

    return templates.TemplateResponse(request, "cache.html", {  # type: ignore[no-any-return]
        "entries": entries,
    })


@router.post("/cache/invalidate")
async def invalidate_all_cache(request: Request) -> RedirectResponse:
    cache = request.app.state.cache
    refresh_cache_impl(cache)
    return RedirectResponse(url="/cache", status_code=303)


@router.post("/cache/invalidate/{tool_name}")
async def invalidate_tool_cache(request: Request, tool_name: str) -> RedirectResponse:
    cache = request.app.state.cache
    refresh_cache_impl(cache, tool_name=tool_name)
    return RedirectResponse(url="/cache", status_code=303)

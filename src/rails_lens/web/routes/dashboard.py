"""ダッシュボードTOP ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.tools.list_models import list_models_impl

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_top(request: Request) -> HTMLResponse:
    bridge = request.app.state.bridge
    cache = request.app.state.cache
    config = request.app.state.config
    templates = request.app.state.templates

    models_output = await list_models_impl(bridge, config)
    cache_stats = cache.get_stats() if hasattr(cache, "get_stats") else {}

    return templates.TemplateResponse(request, "index.html", {  # type: ignore[no-any-return]
        "model_count": len(models_output.models),
        "cache_stats": cache_stats,
        "project_root": getattr(config, "rails_root", ""),
        "version": _get_version(),
    })


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("rails-lens")
    except Exception:
        return "0.1.0"

"""モデル一覧・詳細 ルート"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from rails_lens.models import IntrospectModelInput, TraceCallbackChainInput
from rails_lens.tools.introspect_model import introspect_model_impl
from rails_lens.tools.list_models import list_models_impl
from rails_lens.tools.trace_callback_chain import trace_callback_chain_impl

router = APIRouter()

_CALLBACK_EVENTS = ["before_save", "after_save", "before_create", "after_create"]


@router.get("/models", response_class=HTMLResponse)
async def models_list(request: Request) -> HTMLResponse:
    bridge = request.app.state.bridge
    config = request.app.state.config
    templates = request.app.state.templates

    models_output = await list_models_impl(bridge, config)

    return templates.TemplateResponse(request, "models.html", {  # type: ignore[no-any-return]
        "models": [m.model_dump() for m in models_output.models],
    })


@router.get("/models/{model_name}", response_class=HTMLResponse)
async def model_detail(request: Request, model_name: str) -> HTMLResponse:
    bridge = request.app.state.bridge
    cache = request.app.state.cache
    config = request.app.state.config
    templates = request.app.state.templates

    model_data = await introspect_model_impl(
        IntrospectModelInput(model_name=model_name, sections=None),
        bridge,
        cache,
        config,
    )

    callback_chains: dict[str, object] = {}
    for event in _CALLBACK_EVENTS:
        try:
            result = await trace_callback_chain_impl(
                TraceCallbackChainInput(model_name=model_name, lifecycle_event=event),
                bridge,
                cache,
            )
            callback_chains[event] = result.model_dump()
        except Exception:
            callback_chains[event] = None

    return templates.TemplateResponse(request, "model_detail.html", {  # type: ignore[no-any-return]
        "model": model_data,
        "callback_chains": callback_chains,
    })

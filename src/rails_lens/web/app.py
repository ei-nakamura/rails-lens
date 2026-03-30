"""rails-lens Web ダッシュボード アプリケーションファクトリ"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


def create_app(bridge: Any, cache: Any, config: Any) -> FastAPI:
    """FastAPI アプリケーションを生成して返す"""
    app = FastAPI(title="rails-lens Dashboard")

    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 依存注入
    app.state.bridge = bridge
    app.state.cache = cache
    app.state.config = config
    app.state.templates = templates

    # ルーター登録
    from rails_lens.web.routes import cache as cache_route
    from rails_lens.web.routes import (
        dashboard,
        er,
        flow,
        gems,
        graph,
        health,
        impact,
        models,
        refactor,
    )

    app.include_router(dashboard.router)
    app.include_router(models.router)
    app.include_router(er.router)
    app.include_router(graph.router)
    app.include_router(cache_route.router)
    app.include_router(health.router)
    app.include_router(flow.router)
    app.include_router(impact.router)
    app.include_router(refactor.router)
    app.include_router(gems.router)

    return app


def _build_default_app() -> FastAPI:
    """uvicorn から直接起動するためのデフォルトアプリを生成する"""
    from rails_lens.bridge.runner import RailsBridge
    from rails_lens.cache.manager import CacheManager
    from rails_lens.config import load_config

    config = load_config()
    bridge = RailsBridge(config)
    cache = CacheManager(config)
    return create_app(bridge, cache, config)


with contextlib.suppress(Exception):
    app = _build_default_app()

"""tests/test_web/test_app.py — Web ダッシュボード app/routes テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from rails_lens.models import ListModelsOutput
from rails_lens.web.app import create_app


def _make_app() -> tuple:
    mock_bridge = MagicMock()
    mock_cache = MagicMock()
    mock_cache.get_stats.return_value = {}
    mock_cache.list_entries.return_value = []
    mock_config = MagicMock()
    mock_config.rails_root = "/fake/rails"
    app = create_app(mock_bridge, mock_cache, mock_config)
    return app, mock_bridge, mock_cache, mock_config


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    empty_result = AsyncMock(return_value=ListModelsOutput(models=[]))
    monkeypatch.setattr("rails_lens.web.routes.dashboard.list_models_impl", empty_result)
    monkeypatch.setattr("rails_lens.web.routes.models.list_models_impl", empty_result)
    monkeypatch.setattr("rails_lens.web.routes.er.list_models_impl", empty_result)
    app, _, _, _ = _make_app()
    return TestClient(app)


def test_create_app() -> None:
    app, _, _, _ = _make_app()
    assert app is not None
    assert app.title == "rails-lens Dashboard"


def test_get_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_get_models(client: TestClient) -> None:
    response = client.get("/models")
    assert response.status_code == 200


def test_get_er(client: TestClient) -> None:
    response = client.get("/er")
    assert response.status_code == 200


def test_get_cache(client: TestClient) -> None:
    response = client.get("/cache")
    assert response.status_code == 200

"""tests/test_web/test_refactor.py — /refactor ルートテスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from rails_lens.models import DeadCodeOutput, ExtractConcernOutput
from rails_lens.web.app import create_app


def _make_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mock_bridge = MagicMock()
    mock_cache = MagicMock()
    mock_cache.get_stats.return_value = {}
    mock_cache.list_entries.return_value = []
    mock_config = MagicMock()
    mock_config.rails_root = "/fake/rails"

    monkeypatch.setattr(
        "rails_lens.web.routes.refactor.extract_concern_impl",
        AsyncMock(return_value=ExtractConcernOutput(model_name="User")),
    )
    monkeypatch.setattr(
        "rails_lens.web.routes.refactor.dead_code_impl",
        AsyncMock(return_value=DeadCodeOutput(scope="models")),
    )
    monkeypatch.setattr(
        "rails_lens.web.routes.dashboard.list_models_impl",
        AsyncMock(return_value=MagicMock(models=[])),
    )
    monkeypatch.setattr(
        "rails_lens.web.routes.models.list_models_impl",
        AsyncMock(return_value=MagicMock(models=[])),
    )
    monkeypatch.setattr(
        "rails_lens.web.routes.er.list_models_impl",
        AsyncMock(return_value=MagicMock(models=[])),
    )

    app = create_app(mock_bridge, mock_cache, mock_config)
    return TestClient(app)


def test_get_refactor(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch)
    response = client.get("/refactor/User")
    assert response.status_code == 200

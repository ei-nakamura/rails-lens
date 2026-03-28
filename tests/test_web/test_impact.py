"""tests/test_web/test_impact.py — /impact ルートテスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from rails_lens.models import ImpactAnalysisOutput
from rails_lens.web.app import create_app


def _make_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mock_bridge = MagicMock()
    mock_cache = MagicMock()
    mock_cache.get_stats.return_value = {}
    mock_cache.list_entries.return_value = []
    mock_config = MagicMock()
    mock_config.rails_root = "/fake/rails"

    monkeypatch.setattr(
        "rails_lens.web.routes.impact.impact_analysis_impl",
        AsyncMock(
            return_value=ImpactAnalysisOutput(
                model_name="User",
                target="email",
                change_type="modify",
                target_type="column",
            )
        ),
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


def test_get_impact(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch)
    response = client.get("/impact/User")
    assert response.status_code == 200


def test_get_impact_with_target(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch)
    response = client.get("/impact/User?target=email&change_type=modify")
    assert response.status_code == 200

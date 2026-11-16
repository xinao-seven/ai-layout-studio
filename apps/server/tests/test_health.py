"""Health endpoint contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_returns_contract(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "version": "0.1.0",
        "model": "test-model",
    }


def test_health_is_exposed_in_openapi(test_settings: Settings) -> None:
    app = create_app(test_settings)
    schema = app.openapi()

    assert schema["openapi"].startswith("3.1")
    assert "/api/health" in schema["paths"]
    assert schema["info"]["title"] == test_settings.app_name


def test_root_advertises_docs_path(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["docs"] == "/api/docs"

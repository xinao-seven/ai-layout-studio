"""Floor-plan photo import endpoint tests (PRD FR-19)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.main import create_app
from app.vlm.client import ScriptedVLMClient


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


def vlm_client(
    test_settings: Settings,
    responses: list[str | dict],
) -> TestClient:
    client = TestClient(
        create_app(test_settings, vlm_client=ScriptedVLMClient(responses))
    )
    return client


def jpeg_bytes(width: int = 320, height: int = 240) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 235)).save(buffer, format="JPEG")
    return buffer.getvalue()


GOOD_VLM_JSON = {
    "rooms": [
        {"name": "客厅", "type": "living", "polygon": [[0, 0], [5, 0], [5, 4], [0, 4]]},
        {"name": "卧室", "type": "bedroom", "polygon": [[5, 0], [9, 0], [9, 4], [5, 4]]},
    ]
}


def test_import_photo_requires_vlm(client: TestClient) -> None:
    response = client.post(
        "/api/scenes/import_photo",
        files={"file": ("plan.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "vlm_unavailable"


def test_import_photo_rejects_non_image(client: TestClient) -> None:
    response = client.post(
        "/api/scenes/import_photo",
        files={"file": ("plan.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"photo_invalid", "photo_unsupported"}


def test_import_photo_rejects_wrong_format(test_settings: Settings) -> None:
    with vlm_client(test_settings, [GOOD_VLM_JSON]) as client:
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64)).save(buffer, format="BMP")
        response = client.post(
            "/api/scenes/import_photo",
            files={"file": ("plan.bmp", buffer.getvalue(), "image/bmp")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "photo_unsupported"


def test_import_photo_returns_draft_and_round_trips(test_settings: Settings) -> None:
    with vlm_client(test_settings, [GOOD_VLM_JSON]) as client:
        response = client.post(
            "/api/scenes/import_photo",
            files={"file": ("plan.jpg", jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["document"]["rooms"]) == 2
        assert payload["document"]["rooms"][0]["name"] == "客厅"
        assert payload["document"]["items"] == []

        # The draft imports cleanly as a real scene.
        imported = client.post(
            "/api/scenes/import", json={"document": payload["document"]}
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["id"] != payload["document"]["id"]


def test_import_photo_reports_parse_failure(test_settings: Settings) -> None:
    with vlm_client(test_settings, ["I see a nice apartment, good luck!"]) as client:
        response = client.post(
            "/api/scenes/import_photo",
            files={"file": ("plan.jpg", jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "parse_failed"


def test_import_photo_drops_invalid_rooms_with_warnings(test_settings: Settings) -> None:
    payload = {
        "rooms": [
            {"name": "客厅", "type": "living", "polygon": [[0, 0], [5, 0], [5, 4], [0, 4]]},
            {"name": "坏房间", "type": "bedroom", "polygon": [[0, 0], [1, 1], [1, 0], [0, 1]]},
            {"name": "太小", "type": "study", "polygon": [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]},
            "junk",
        ]
    }
    with vlm_client(test_settings, [json.dumps(payload, ensure_ascii=False)]) as client:
        response = client.post(
            "/api/scenes/import_photo",
            files={"file": ("plan.jpg", jpeg_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["document"]["rooms"]) == 1
        assert body["document"]["rooms"][0]["name"] == "客厅"
        assert len(body["warnings"]) >= 2

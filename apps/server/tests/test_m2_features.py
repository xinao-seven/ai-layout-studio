"""M2 asset/catalog integrity and editing API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.catalog.assets import MIN_CATALOG_ENTRIES, validate_catalog_assets
from app.catalog.service import CatalogService
from app.core.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def client(test_settings: Settings):
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


def test_catalog_assets_are_normalized_and_routable() -> None:
    errors = validate_catalog_assets(
        REPO_ROOT / "fixtures" / "catalog.json",
        REPO_ROOT / "assets",
    )
    assert not errors, "\n".join(errors)
    entries = CatalogService().entries
    assert len(entries) >= MIN_CATALOG_ENTRIES
    assert len(entries) <= 60
    first = entries[0]
    assert first.variants[0].model_path.startswith("/assets/models/")
    assert first.variants[0].thumbnail_path.startswith("/assets/thumbnails/")


def test_asset_static_mount_serves_models_and_thumbnails(client: TestClient) -> None:
    model = client.get("/assets/models/sofa.glb")
    assert model.status_code == 200
    assert model.headers["content-type"] in ("model/gltf-binary", "application/octet-stream")
    assert model.content[:4] == b"glTF"

    thumbnail = client.get("/assets/thumbnails/sofa.png")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/png"


def test_catalog_reload_endpoint(client: TestClient) -> None:
    response = client.post("/api/catalog/reload")
    assert response.status_code == 200, response.text
    entries = response.json()["entries"]
    assert len(entries) >= MIN_CATALOG_ENTRIES
    assert any(entry["kindId"] == "sofa" for entry in entries)
    assert any(entry["rules"] and entry["rules"].get("allowOverlap") for entry in entries)


def create_studio(client: TestClient) -> dict:
    response = client.post("/api/scenes", json={"fixtureId": "studio"})
    assert response.status_code == 201, response.text
    return response.json()


def test_collision_refusal_is_readable(client: TestClient) -> None:
    document = create_studio(client)
    response = client.post(
        f"/api/scenes/{document['id']}/items",
        json={"kindId": "plant", "position": [6.2, 4.2]},
    )
    assert response.status_code == 400, response.text
    body = response.json()["error"]
    assert body["code"] == "collision"
    assert body["suggest"]
    assert body["details"]["itemIds"]


def test_blocks_door_refusal(client: TestClient) -> None:
    document = create_studio(client)
    response = client.post(
        f"/api/scenes/{document['id']}/items",
        json={"kindId": "plant", "position": [0.4, 2.5]},
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "blocks_door"
    assert error["suggest"]


def test_room_edit_reanchors_opening_and_returns_warning(client: TestClient) -> None:
    document = create_studio(client)
    bath = next(room for room in document["rooms"] if room["type"] == "bathroom")
    assert bath["polygon"] == [[8, 0], [10.5, 0], [10.5, 3], [8, 3]]
    window = next(
        opening
        for opening in document["openings"]
        if opening["roomId"] == bath["id"] and opening["type"] == "window"
    )

    response = client.post(
        f"/api/scenes/{document['id']}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "room.update",
                    "roomId": bath["id"],
                    "after": {"polygon": [[8, 0], [10.6, 0], [10.6, 3], [8, 3]]},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"], body
    assert body["warnings"][0]["code"] == "opening_reanchored"
    reanchored = next(
        opening for opening in body["scene"]["openings"] if opening["id"] == window["id"]
    )
    assert reanchored["edgeIndex"] == 1
    assert reanchored["offset"] == 1.5


def test_catalog_reload_keeps_registry_valid(client: TestClient) -> None:
    document = create_studio(client)
    assert client.post("/api/catalog/reload").status_code == 200
    response = client.post(
        f"/api/scenes/{document['id']}/items",
        json={"kindId": "plant", "roomId": document["rooms"][0]["id"]},
    )
    assert response.status_code == 200, response.text


def test_manual_living_room_layout(client: TestClient) -> None:
    """M2 exit condition: sofa + coffee table + TV stand + rug, no collision."""
    created = client.post("/api/scenes", json={"name": "Manual living room"})
    assert created.status_code == 201, created.text
    scene_id = created.json()["id"]
    room_id = "room_manual_living"
    ops: list[dict] = [
        {
            "op": "room.add",
            "room": {
                "id": room_id,
                "name": "客厅",
                "type": "living",
                "polygon": [[0, 0], [6, 0], [6, 5], [0, 5]],
            },
        }
    ]
    placements = [
        ("it_manual_sofa", "sofa", [3.0, 0, 4.2], 3.141592653589793),
        ("it_manual_coffee", "coffee_table", [3.0, 0, 3.1], 0.0),
        ("it_manual_tv", "tv_stand", [3.0, 0, 0.5], 3.141592653589793),
        ("it_manual_rug", "rug", [3.0, 0, 3.25], 0.0),
    ]
    for item_id, kind_id, position, rotation in placements:
        ops.append(
            {
                "op": "item.add",
                "item": {
                    "id": item_id,
                    "kindId": kind_id,
                    "variantId": "default",
                    "transform": {"position": position, "rotationY": rotation},
                    "constraint": {"roomId": room_id},
                },
            }
        )

    response = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={"baseVersion": 1, "ops": ops},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scene"]["version"] == 2
    kinds = [item["kindId"] for item in body["scene"]["items"]]
    assert kinds == ["sofa", "coffee_table", "tv_stand", "rug"]


def test_catalog_reload_rejects_catalog_that_breaks_live_scene(client: TestClient) -> None:
    document = create_studio(client)
    assert document["items"]
    catalog_service = client.app.state.catalog  # type: ignore[attr-defined]
    original_preview = catalog_service.preview
    catalog_service.preview = lambda path=None: [  # type: ignore[method-assign]
        entry for entry in original_preview(path) if entry.kind_id != "sofa"
    ]
    try:
        response = client.post("/api/catalog/reload")
    finally:
        catalog_service.preview = original_preview  # type: ignore[method-assign]
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_input"
    assert "live scene" in response.json()["error"]["message"]
    # The live catalog was not swapped.
    assert any(entry["kindId"] == "sofa" for entry in client.get("/api/catalog").json()["entries"])

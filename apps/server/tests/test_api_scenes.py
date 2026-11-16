"""REST scene API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


def create_studio(client: TestClient) -> dict:
    response = client.post("/api/scenes", json={"fixtureId": "studio"})
    assert response.status_code == 201, response.text
    return response.json()


def item_payload(item_id: str = "it_api_plant", position: list[float] | None = None) -> dict:
    return {
        "id": item_id,
        "kindId": "plant",
        "variantId": "default",
        "transform": {"position": position or [4.0, 0.0, 2.5], "rotationY": 0},
        "source": "manual",
    }


def test_fixtures_and_catalog_endpoints(client: TestClient) -> None:
    fixtures = client.get("/api/fixtures")
    assert fixtures.status_code == 200
    payload = fixtures.json()
    assert len(payload["fixtures"]) == 6
    assert payload["fixtures"][0]["areaM2"] > 20

    catalog = client.get("/api/catalog")
    assert catalog.status_code == 200
    entries = catalog.json()["entries"]
    assert any(entry["kindId"] == "sofa" for entry in entries)
    assert all(entry["variants"] for entry in entries)


def test_create_list_get_and_restart(client: TestClient, test_settings: Settings) -> None:
    document = create_studio(client)
    assert document["id"].startswith("scene_")
    assert document["schemaVersion"] == 1
    assert document["rooms"][0]["polygon"][0] == [0, 0]
    assert "createdAt" in document

    listed = client.get("/api/scenes")
    assert listed.status_code == 200
    scenes = listed.json()["scenes"]
    assert len(scenes) == 1
    assert scenes[0]["id"] == document["id"]
    assert scenes[0]["roomCount"] == 2
    assert scenes[0]["areaM2"] == pytest.approx(47.5)

    fetched = client.get(f"/api/scenes/{document['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == document["name"]

    # A fresh app against the same data dir recovers the scene from SQLite.
    with TestClient(create_app(test_settings)) as restarted:
        again = restarted.get(f"/api/scenes/{document['id']}")
        assert again.status_code == 200
        assert again.json()["version"] == 1


def test_commit_undo_redo_and_export(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]

    committed = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [{"op": "item.add", "item": item_payload()}],
        },
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["scene"]["version"] == 2
    assert result["patch"]["fromVersion"] == 1
    assert result["patch"]["toVersion"] == 2
    assert result["patch"]["ops"][0]["op"] == "item.add"
    assert any(item["id"] == "it_api_plant" for item in result["scene"]["items"])

    undone = client.post(f"/api/scenes/{scene_id}/undo", json={"expectedVersion": 2})
    assert undone.status_code == 200, undone.text
    undone_body = undone.json()
    assert undone_body["scene"]["version"] == 3
    assert all(item["id"] != "it_api_plant" for item in undone_body["scene"]["items"])

    redone = client.post(f"/api/scenes/{scene_id}/redo", json={"expectedVersion": 3})
    assert redone.status_code == 200, redone.text
    assert redone.json()["scene"]["version"] == 4
    assert any(item["id"] == "it_api_plant" for item in redone.json()["scene"]["items"])

    exported = client.get(f"/api/scenes/{scene_id}/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    exported_doc = exported.json()
    assert exported_doc["schemaVersion"] == 1
    assert exported_doc["version"] == 4


def test_import_remaps_ids_and_creates_new_scene(client: TestClient) -> None:
    document = create_studio(client)
    exported = client.get(f"/api/scenes/{document['id']}/export").json()

    imported = client.post("/api/scenes/import", json={"document": exported})
    assert imported.status_code == 201, imported.text
    imported_doc = imported.json()
    assert imported_doc["id"] != exported["id"]
    assert imported_doc["version"] == 1
    assert len(imported_doc["rooms"]) == len(exported["rooms"])
    assert imported_doc["rooms"][0]["id"] != exported["rooms"][0]["id"]
    assert imported_doc["items"][0]["id"] != exported["items"][0]["id"]
    # Opening and item references follow the remapped room ids.
    room_ids = {room["id"] for room in imported_doc["rooms"]}
    assert all(opening["roomId"] in room_ids for opening in imported_doc["openings"])


def test_stale_commit_returns_version_conflict(client: TestClient) -> None:
    document = create_studio(client)
    committed = client.post(
        f"/api/scenes/{document['id']}/commit",
        json={"baseVersion": 99, "ops": [{"op": "item.add", "item": item_payload()}]},
    )
    assert committed.status_code == 409
    body = committed.json()
    assert body["error"]["code"] == "version_conflict"
    assert body["error"]["suggest"]


def test_invalid_polygon_is_rejected(client: TestClient) -> None:
    document = create_studio(client)
    response = client.post(
        f"/api/scenes/{document['id']}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "room.add",
                    "room": {
                        "id": "room_bad",
                        "name": "Bad",
                        "type": "living",
                        "polygon": [[0, 0], [2, 2], [2, 0], [0, 2]],
                    },
                }
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_polygon"


def test_request_validation_uses_error_envelope(client: TestClient) -> None:
    response = client.post("/api/scenes", json={"fixtureId": "studio", "unexpected": True})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_unknown_scene_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/api/scenes/scene_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]


def test_export_json_has_camel_case_and_round_trip(client: TestClient) -> None:
    document = create_studio(client)
    exported = client.get(f"/api/scenes/{document['id']}/export").json()

    assert "schemaVersion" in exported
    assert "wallHeight" in exported["defaults"]
    assert "createdAt" in exported
    assert "edgeIndex" in exported["openings"][0]
    # JSON round-trips through the scene contract.
    assert json.loads(json.dumps(exported)) == exported


def test_add_move_delete_item_endpoints_and_undo(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    room_id = document["rooms"][0]["id"]

    added = client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "roomId": room_id},
    )
    assert added.status_code == 200, added.text
    result = added.json()
    assert result["scene"]["version"] == 2
    new_item = next(
        item
        for item in result["scene"]["items"]
        if item["kindId"] == "plant"
        and item["id"] not in {candidate["id"] for candidate in document["items"]}
    )
    assert new_item["id"].startswith("it_")
    assert new_item["constraint"]["roomId"] == room_id

    moved = client.patch(
        f"/api/scenes/{scene_id}/items/{new_item['id']}",
        json={"position": [0.7, 0.0, 1.0], "rotationY": 1.5707963267948966},
    )
    assert moved.status_code == 200, moved.text
    moved_item = next(
        item for item in moved.json()["scene"]["items"] if item["id"] == new_item["id"]
    )
    assert moved_item["transform"]["position"] == [0.7, 0.0, 1.0]
    assert moved_item["transform"]["rotationY"] == pytest.approx(1.5707963267948966)

    deleted = client.delete(f"/api/scenes/{scene_id}/items/{new_item['id']}")
    assert deleted.status_code == 200, deleted.text
    assert all(item["id"] != new_item["id"] for item in deleted.json()["scene"]["items"])

    undone = client.post(f"/api/scenes/{scene_id}/undo", json={"expectedVersion": 4})
    assert undone.status_code == 200
    assert any(item["id"] == new_item["id"] for item in undone.json()["scene"]["items"])


def test_add_item_rejects_unknown_kind_and_outside_position(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]

    unknown = client.post(f"/api/scenes/{scene_id}/items", json={"kindId": "hoverboard"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "unknown_kind"

    outside = client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "position": [99.0, 99.0]},
    )
    assert outside.status_code == 400
    assert outside.json()["error"]["code"] == "out_of_room"


def test_auto_placement_finds_distinct_free_positions(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    room_id = document["rooms"][0]["id"]

    positions = []
    for _ in range(2):
        response = client.post(
            f"/api/scenes/{scene_id}/items",
            json={"kindId": "plant", "roomId": room_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        added = next(op for op in body["patch"]["ops"] if op["op"] == "item.add")
        positions.append(tuple(added["item"]["transform"]["position"][::2]))
    assert positions[0] != positions[1]


def test_invalid_partial_update_returns_invalid_input(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    item_id = document["items"][0]["id"]

    client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "roomId": document["rooms"][0]["id"]},
    )
    added = client.get(f"/api/scenes/{scene_id}").json()["items"][-1]

    response = client.patch(
        f"/api/scenes/{scene_id}/items/{added['id']}",
        json={"position": [0.7, 0.0, 1.0]},
    )
    assert response.status_code == 200

    invalid = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": response.json()["scene"]["version"],
            "ops": [
                {
                    "op": "item.update",
                    "itemId": item_id,
                    "after": {"transform": {"unknownField": 1}},
                }
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_input"


def test_import_rejects_dangling_opening_room(client: TestClient) -> None:
    document = create_studio(client)
    exported = client.get(f"/api/scenes/{document['id']}/export").json()
    exported["openings"][0]["roomId"] = "room_missing"

    response = client.post("/api/scenes/import", json={"document": exported})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "import_invalid"


def test_import_rejects_dangling_item_reference(client: TestClient) -> None:
    document = create_studio(client)
    exported = client.get(f"/api/scenes/{document['id']}/export").json()
    item = next(candidate for candidate in exported["items"] if candidate.get("constraint"))
    item["constraint"] = {"near": {"itemId": "it_missing", "distance": 0.5}}

    response = client.post("/api/scenes/import", json={"document": exported})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "import_invalid"


def test_concave_room_placement_does_not_500(client: TestClient) -> None:
    created = client.post("/api/scenes", json={"name": "Concave"})
    scene_id = created.json()["id"]
    bridged = [
        [0, 0],
        [2, 0],
        [2, 0.75],
        [2.5, 0.75],
        [2.5, 0],
        [4.5, 0],
        [4.5, 2],
        [2.5, 2],
        [2.5, 1.25],
        [2, 1.25],
        [2, 2],
        [0, 2],
    ]
    committed = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "room.add",
                    "room": {
                        "id": "room_bridge",
                        "name": "Bridge",
                        "type": "living",
                        "polygon": bridged,
                    },
                }
            ],
        },
    )
    assert committed.status_code == 200, committed.text
    response = client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "roomId": "room_bridge"},
    )
    assert response.status_code != 500, response.text


def test_narrow_room_placement_returns_room_too_small(client: TestClient) -> None:
    created = client.post("/api/scenes", json={"name": "Narrow"})
    scene_id = created.json()["id"]
    committed = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "room.add",
                    "room": {
                        "id": "room_narrow",
                        "name": "Narrow",
                        "type": "other",
                        "polygon": [[0, 0], [0.11, 0], [0.11, 10], [0, 10]],
                    },
                }
            ],
        },
    )
    assert committed.status_code == 200, committed.text
    response = client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "roomId": "room_narrow"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "room_too_small"


def test_add_item_position_must_be_inside_requested_room(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    bathroom_id = document["rooms"][1]["id"]

    response = client.post(
        f"/api/scenes/{scene_id}/items",
        json={"kindId": "plant", "roomId": bathroom_id, "position": [2.0, 2.0]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "out_of_room"


def test_move_item_updates_constraint_room(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    bathroom_id = document["rooms"][1]["id"]
    bedroom_item = next(
        item
        for item in document["items"]
        if item["constraint"] and item["constraint"].get("roomId")
    )

    moved = client.patch(
        f"/api/scenes/{scene_id}/items/{bedroom_item['id']}",
        json={"position": [9.5, 0.0, 1.5]},
    )

    assert moved.status_code == 200, moved.text
    updated = next(
        item for item in moved.json()["scene"]["items"] if item["id"] == bedroom_item["id"]
    )
    assert updated["constraint"]["roomId"] == bathroom_id


def test_item_update_cannot_change_identity(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    item_id = document["items"][0]["id"]

    response = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "item.update",
                    "itemId": item_id,
                    "after": {"id": "it_other"},
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_import_unknown_kind_does_not_create_scene(client: TestClient) -> None:
    document = create_studio(client)
    exported = client.get(f"/api/scenes/{document['id']}/export").json()
    exported["items"][0]["kindId"] = "hoverboard"
    before = len(client.get("/api/scenes").json()["scenes"])

    response = client.post("/api/scenes/import", json={"document": exported})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "import_invalid"
    after = len(client.get("/api/scenes").json()["scenes"])
    assert after == before

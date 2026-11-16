"""WebSocket gateway tests: snapshot, broadcast, host commit, resume."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


def create_studio(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/scenes", json={"fixtureId": "studio"})
    assert response.status_code == 201
    return response.json()


def plant_item(item_id: str, x: float = 4.0) -> dict[str, Any]:
    return {
        "id": item_id,
        "kindId": "plant",
        "variantId": "default",
        "transform": {"position": [x, 0.0, 2.5], "rotationY": 0},
        "source": "manual",
    }


def test_ws_snapshot_and_rest_commit_broadcast(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]

    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["ch"] == "state"
        assert snapshot["type"] == "scene.snapshot"
        assert snapshot["seq"] == 1
        assert snapshot["payload"]["scene"]["id"] == scene_id

        committed = client.post(
            f"/api/scenes/{scene_id}/commit",
            json={
                "baseVersion": 1,
                "ops": [{"op": "item.add", "item": plant_item("it_ws_a")}],
            },
        )
        assert committed.status_code == 200
        patch = websocket.receive_json()
        assert patch["ch"] == "state"
        assert patch["type"] == "scene.patch"
        assert patch["seq"] == 2
        assert patch["payload"]["patch"]["toVersion"] == 2


def test_ws_host_commit_and_ack(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]

    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()  # snapshot
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "commit",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {
                    "baseVersion": 1,
                    "ops": [{"op": "item.add", "item": plant_item("it_ws_b")}],
                },
            }
        )
        patch = websocket.receive_json()
        ack = websocket.receive_json()
        assert patch["type"] == "scene.patch"
        assert patch["payload"]["patch"]["toVersion"] == 2
        assert ack["ch"] == "host"
        assert ack["type"] == "host.ack"
        assert ack["payload"]["version"] == 2

    fetched = client.get(f"/api/scenes/{scene_id}").json()
    assert fetched["version"] == 2
    assert any(item["id"] == "it_ws_b" for item in fetched["items"])


def test_ws_ping_pong_and_invalid_message(client: TestClient) -> None:
    document = create_studio(client)

    with client.websocket_connect(f"/ws?sceneId={document['id']}") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "ping",
                "seq": 0,
                "ts": 123,
                "payload": {},
            }
        )
        pong = websocket.receive_json()
        assert pong["type"] == "host.pong"
        assert pong["payload"]["ts"] == 123

        websocket.send_json({"unexpected": True})
        error = websocket.receive_json()
        assert error["type"] == "host.error"
        assert error["payload"]["error"]["code"] == "invalid_input"


def test_ws_resume_replays_missed_patch(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]

    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        snapshot = websocket.receive_json()
        first_seq = snapshot["seq"]

    committed = client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [{"op": "item.add", "item": plant_item("it_ws_resume")}],
        },
    )
    assert committed.status_code == 200

    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()  # fresh snapshot
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "resume",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"lastSeqs": {"state": first_seq}},
            }
        )
        replayed = websocket.receive_json()
        assert replayed["type"] == "scene.patch"
        assert replayed["payload"]["patch"]["toVersion"] == 2
        ack = websocket.receive_json()
        assert ack["type"] == "host.ack"
        assert ack["payload"]["type"] == "resume"


def test_ws_rejects_unknown_scene(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws?sceneId=scene_missing") as websocket,
    ):
        websocket.receive_json()


def test_ws_ack_includes_reanchor_warnings(client: TestClient) -> None:
    document = create_studio(client)
    scene_id = document["id"]
    bath = next(room for room in document["rooms"] if room["type"] == "bathroom")

    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()  # snapshot
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "commit",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {
                    "baseVersion": 1,
                    "ops": [
                        {
                            "op": "room.update",
                            "roomId": bath["id"],
                            "after": {"polygon": [[8, 0], [10.6, 0], [10.6, 3], [8, 3]]},
                        }
                    ],
                },
            }
        )
        patch = websocket.receive_json()
        ack = websocket.receive_json()
        assert patch["type"] == "scene.patch"
        assert ack["type"] == "host.ack"
        warnings = ack["payload"]["warnings"]
        assert any(warning["code"] == "opening_reanchored" for warning in warnings)

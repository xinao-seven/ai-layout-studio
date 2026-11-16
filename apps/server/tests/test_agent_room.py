"""add_room agent tool tests: operation unit tests + scripted end-to-end."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.contracts import AddRoomInput
from app.agent.llm import ScriptedAdapter
from app.agent.room_ops import plan_add_room
from app.contracts.enums import RoomType
from app.contracts.rules import get_rules
from app.core.config import Settings
from app.main import create_app
from app.scene.factory import empty_scene


def make_client(settings: Settings, steps: list[dict[str, Any]]) -> TestClient:
    return TestClient(create_app(settings, llm_adapter=ScriptedAdapter(steps)))


@pytest.fixture
def room_client(test_settings: Settings) -> Iterator[TestClient]:
    steps: list[dict[str, Any]] = [
        {
            "text": "好的，我先看一下当前场景。",
            "toolCalls": [{"id": "room-call-1", "name": "query_scene", "arguments": {}}],
        },
        {
            "text": "",
            "toolCalls": [
                {
                    "id": "room-call-2",
                    "name": "add_room",
                    "arguments": {
                        "name": "卧室",
                        "type": "bedroom",
                        "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
                    },
                }
            ],
        },
        {"text": "已生成卧室提案，请确认。"},
    ]
    with TestClient(create_app(test_settings, llm_adapter=ScriptedAdapter(steps))) as client:
        yield client


def wait_turn(
    client: TestClient, scene_id: str, turn_id: str, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/scenes/{scene_id}/turns/{turn_id}")
        assert response.status_code == 200, response.text
        last = response.json()["turn"]
        if last["status"] in {"done", "failed", "cancelled"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"turn did not finish: {last}")


def receive_until(client: TestClient, websocket: Any, message_type: str) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    for _ in range(20):
        frame = websocket.receive_json()
        received.append(frame)
        if frame.get("type") == message_type:
            return received
        if frame.get("type") == "host.error":
            return received
    raise AssertionError(f"did not receive {message_type}: {received}")


# ---- operation layer (pure functions) ---------------------------------------


def test_plan_add_room_snaps_and_normalizes_winding() -> None:
    document = empty_scene()
    # Clockwise + off-grid vertices: server must fix winding and snap to 0.1 m.
    request = AddRoomInput(
        name="书房",
        type=RoomType.STUDY,
        polygon=[(0.02, 3.03), (4.04, 2.98), (4.0, 0.0), (0.0, 0.02)],
    )
    result = plan_add_room(document, request, rules=get_rules())
    assert result.ok
    assert result.refusals == []
    assert result.room is not None
    assert result.room.polygon == [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
    assert result.document_after is not None
    assert [room.name for room in result.document_after.rooms] == ["书房"]


def test_plan_add_room_defaults_name_and_dedupes_vertices() -> None:
    document = empty_scene()
    request = AddRoomInput(polygon=[(0, 0), (2, 0), (2, 2), (2, 2.01), (2.02, 2), (0, 2), (0, 0)])
    result = plan_add_room(document, request, rules=get_rules())
    assert result.ok
    assert result.room is not None
    assert result.room.name == "房间 1"
    assert result.room.type == RoomType.OTHER
    # The wrapped duplicate at (0,0) and consecutive duplicates are dropped.
    assert result.room.polygon == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]


def test_plan_add_room_counts_existing_rooms_in_default_name() -> None:
    document = empty_scene()
    first = plan_add_room(
        document, AddRoomInput(polygon=[(0, 0), (2, 0), (2, 2), (0, 2)]), rules=get_rules()
    )
    assert first.room is not None
    document_after = first.document_after
    assert document_after is not None
    second = plan_add_room(
        document_after,
        AddRoomInput(polygon=[(5, 0), (7, 0), (7, 2), (5, 2)]),
        rules=get_rules(),
    )
    assert second.room is not None
    assert second.room.name == "房间 2"


def test_plan_add_room_refuses_collapsed_outline() -> None:
    document = empty_scene()
    # After grid snapping only two distinct vertices remain (< 3).
    request = AddRoomInput(polygon=[(0.04, 0.04), (0.05, 0.05), (0.06, 0.06)])
    result = plan_add_room(document, request, rules=get_rules())
    assert not result.ok
    assert result.ops == []
    assert result.refusals[0].code == "invalid_polygon"
    assert result.refusals[0].details is not None
    assert result.refusals[0].details["vertexCount"] == 2


def test_plan_add_room_refuses_self_intersecting_outline() -> None:
    document = empty_scene()
    request = AddRoomInput(polygon=[(0, 0), (4, 4), (4, 0), (0, 4)])
    result = plan_add_room(document, request, rules=get_rules())
    assert not result.ok
    assert result.refusals[0].code == "invalid_polygon"


def test_plan_add_room_refuses_too_small_outline() -> None:
    document = empty_scene()
    request = AddRoomInput(polygon=[(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)])
    result = plan_add_room(document, request, rules=get_rules())
    assert not result.ok
    assert result.refusals[0].code == "room_too_small"


# ---- scripted end-to-end: proposal -> confirm -> undo ------------------------


def test_add_room_scripted_turn_proposal_confirm_and_undo(room_client: TestClient) -> None:
    response = room_client.post("/api/scenes", json={"name": "空白场景"})
    assert response.status_code == 201, response.text
    document = response.json()
    scene_id = document["id"]

    turn_response = room_client.post(
        f"/api/scenes/{scene_id}/turns", json={"message": "帮我画一个 4m×3m 的卧室"}
    )
    assert turn_response.status_code == 201, turn_response.text
    turn = wait_turn(room_client, scene_id, turn_response.json()["turn"]["id"])
    assert turn["status"] == "done", turn
    assert turn["usage"]["refusals"] == [], turn["usage"]

    proposals = room_client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
    ).json()["proposals"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["ops"][0]["op"] == "room.add"
    assert proposal["ops"][0]["room"]["name"] == "卧室"
    assert proposal["ops"][0]["room"]["type"] == "bedroom"

    with room_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "scene.snapshot"
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"proposalId": proposal["id"]},
            }
        )
        frames = receive_until(room_client, websocket, "host.ack")
        assert any(
            frame["type"] == "scene.patch" and frame["payload"]["patch"]["toVersion"] == 2
            for frame in frames
        )
        assert any(
            frame["type"] == "proposal.updated"
            and frame["payload"]["proposal"]["status"] == "confirmed"
            for frame in frames
        )

    scene = room_client.get(f"/api/scenes/{scene_id}").json()
    assert scene["version"] == 2
    assert [room["name"] for room in scene["rooms"]] == ["卧室"]
    assert scene["rooms"][0]["polygon"] == [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]

    undone = room_client.post(f"/api/scenes/{scene_id}/undo", json={"expectedVersion": 2})
    assert undone.status_code == 200, undone.text
    assert undone.json()["scene"]["rooms"] == []


def test_add_room_scripted_turn_returns_refusal_without_proposal(
    test_settings: Settings,
) -> None:
    steps: list[dict[str, Any]] = [
        {
            "text": "",
            "toolCalls": [
                {
                    "id": "room-call-1",
                    "name": "add_room",
                    "arguments": {"polygon": [[0, 0], [4, 4], [4, 0], [0, 4]]},
                }
            ],
        },
        {"text": "轮廓自相交了，无法生成提案。"},
    ]
    with make_client(test_settings, steps) as client:
        response = client.post("/api/scenes", json={"name": "空白场景"})
        assert response.status_code == 201, response.text
        scene_id = response.json()["id"]

        turn_response = client.post(f"/api/scenes/{scene_id}/turns", json={"message": "画一个房间"})
        assert turn_response.status_code == 201, turn_response.text
        turn = wait_turn(client, scene_id, turn_response.json()["turn"]["id"])
        assert turn["status"] == "done", turn
        assert turn["usage"]["refusals"], turn["usage"]

        proposals = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert proposals == []
        scene = client.get(f"/api/scenes/{scene_id}").json()
        assert scene["rooms"] == []
        assert scene["version"] == 1


# ---- input hardening (review P1-1) -------------------------------------------


def test_add_room_input_rejects_non_finite_and_out_of_range_coordinates() -> None:
    with pytest.raises(ValidationError):
        AddRoomInput(polygon=[(0, 0), (float("nan"), 0), (4, 3)])
    with pytest.raises(ValidationError):
        AddRoomInput(polygon=[(0, 0), (float("inf"), 0), (4, 3)])
    with pytest.raises(ValidationError):
        AddRoomInput(polygon=[(0, 0), (1000, 0), (1000, 3), (0, 3)])


def test_plan_add_room_snaps_half_values_up_like_the_client() -> None:
    document = empty_scene()
    # 3.45 sits exactly on the .05 boundary: half-up must match the JS client.
    request = AddRoomInput(polygon=[(0, 0), (3.45, 0), (3.45, 2), (0, 2)])
    result = plan_add_room(document, request, rules=get_rules())
    assert result.ok
    assert result.room is not None
    assert result.room.polygon == [(0.0, 0.0), (3.5, 0.0), (3.5, 2.0), (0.0, 2.0)]

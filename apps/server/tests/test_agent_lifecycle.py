"""M3 turn lifecycle: queueing, cancellation, waiting timeout and restart."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.agent.manager as manager_module
from app.agent.llm import ScriptedAdapter
from app.core.config import Settings
from app.main import create_app
from tests.test_agent_m3 import create_living, create_turn, wait_turn


def test_turn_queue_and_cancel(test_settings: Settings) -> None:
    steps = [
        {
            "toolCalls": [
                {
                    "id": "ask-1",
                    "name": "ask_user",
                    "arguments": {"question": "Continue?", "options": ["yes"]},
                }
            ]
        },
        {"text": "second turn done"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        first = create_turn(client, scene_id, "first")
        status = ""
        for _ in range(100):
            status = client.get(f"/api/scenes/{scene_id}/turns/{first}").json()["turn"]["status"]
            if status == "waiting_user":
                break
            time.sleep(0.02)
        assert status == "waiting_user"

        queued = client.post(f"/api/scenes/{scene_id}/turns", json={"message": "second"})
        assert queued.status_code == 201
        assert queued.json()["turn"]["status"] == "queued"
        second = queued.json()["turn"]["id"]

        third = client.post(f"/api/scenes/{scene_id}/turns", json={"message": "third"})
        assert third.status_code == 409
        assert third.json()["error"]["code"] == "turn_in_progress"

        cancelled = client.post(f"/api/turns/{first}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        second_turn = wait_turn(client, scene_id, second)
        assert second_turn["status"] == "done"
        assert client.get(f"/api/scenes/{scene_id}").json()["version"] == 1


def test_waiting_user_timeout_fails_turn(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_module, "WAITING_USER_TIMEOUT_SECONDS", 0.05)
    steps = [
        {
            "toolCalls": [
                {
                    "id": "ask-1",
                    "name": "ask_user",
                    "arguments": {"question": "Still there?", "options": ["yes"]},
                }
            ]
        }
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        turn_id = create_turn(client, scene_id, "wait")
        turn = wait_turn(client, scene_id, turn_id, timeout=3.0)
        assert turn["status"] == "failed"
        error = client.app.state.turn_manager.error_for(turn_id)
        assert error is not None
        assert error["code"] == "tool_timeout"
        assert client.get(f"/api/scenes/{scene_id}").json()["version"] == 1


def test_restart_marks_unfinished_turn_failed_and_proposals_expired(
    test_settings: Settings,
) -> None:
    steps = [
        {
            "toolCalls": [
                {
                    "id": "q-1",
                    "name": "query_scene",
                    "arguments": {"includePending": True},
                }
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "a-1",
                    "name": "add_item",
                    "arguments": {
                        "kindId": "sofa",
                        "roomId": "$lastRoomId",
                        "relation": {"type": "against_wall", "wallLabel": "电视墙"},
                    },
                }
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "ask-1",
                    "name": "ask_user",
                    "arguments": {"question": "Confirm?", "options": ["yes", "no"]},
                }
            ]
        },
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        turn_id = create_turn(client, scene_id, "sofa")
        status = ""
        for _ in range(100):
            turn = client.get(f"/api/scenes/{scene_id}/turns/{turn_id}").json()["turn"]
            status = turn["status"]
            if status == "waiting_user":
                break
            time.sleep(0.02)
        assert status == "waiting_user"
        pending = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert len(pending) == 1

    with TestClient(create_app(test_settings)) as restarted:
        recovered = restarted.get(f"/api/scenes/{scene_id}/turns/{turn_id}").json()["turn"]
        assert recovered["status"] == "failed"
        assert len(recovered["messages"]) > 1
        pending = restarted.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert pending == []
        expired = restarted.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "expired"}
        ).json()["proposals"]
        assert len(expired) == 1
        assert expired[0]["reason"] == "server_restart"


def test_missing_llm_fails_turn_with_llm_error(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        document = create_living(client)
        scene_id = document["id"]
        turn_id = create_turn(client, scene_id, "hello")
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "failed"
        error = client.app.state.turn_manager.error_for(turn_id)
        assert error is not None
        assert error["code"] == "llm_error"


def test_budget_exceeded_fails_turn(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "MAX_ITERATIONS", 1)
    steps = [
        {
            "toolCalls": [
                {
                    "id": "q-1",
                    "name": "query_scene",
                    "arguments": {"includePending": True},
                }
            ]
        }
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        turn_id = create_turn(client, scene_id, "loop")
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "failed"
        error = client.app.state.turn_manager.error_for(turn_id)
        assert error is not None
        assert error["code"] == "budget_exceeded"


def test_reconnect_replays_turn_events_before_status_and_keeps_waiting_prompt(
    test_settings: Settings,
) -> None:
    long_text = "我先检查一下房间的尺寸和可用的家具，然后再向你确认放置位置。"
    steps = [
        {
            "text": long_text,
            "toolCalls": [
                {
                    "id": "ask-1",
                    "name": "ask_user",
                    "arguments": {"question": "放哪面墙？", "options": ["电视墙", "东墙"]},
                }
            ],
        }
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()  # snapshot
            turn_id = create_turn(client, scene_id, "放个沙发")
            first_delta: dict[str, Any] = {}
            for _ in range(10):
                frame = websocket.receive_json()
                if frame["type"] == "assistant.delta":
                    first_delta = frame
                    break
            assert first_delta, "no assistant.delta before reconnect"

        status = ""
        for _ in range(100):
            status = client.get(f"/api/scenes/{scene_id}/turns/{turn_id}").json()["turn"]["status"]
            if status == "waiting_user":
                break
            time.sleep(0.02)
        assert status == "waiting_user"

        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "scene.snapshot"
            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "resume",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "payload": {
                        "lastSeqs": {
                            "state": snapshot["seq"],
                            "turn": first_delta["seq"],
                        }
                    },
                }
            )
            frames: list[dict[str, Any]] = []
            for _ in range(30):
                frame = websocket.receive_json()
                frames.append(frame)
                if frame["type"] == "turn.status":
                    break
            replayed = [
                frame
                for frame in frames
                if frame["ch"] == "turn"
                and frame["type"] == "assistant.delta"
                and frame["seq"] > first_delta["seq"]
            ]
            assert replayed, frames
            status = frames[-1]
            assert status["type"] == "turn.status"
            assert status["payload"]["turn"]["status"] == "waiting_user"
            assert status["payload"]["waiting"]["question"] == "放哪面墙？"
            assert len(status["payload"]["turn"]["messages"]) > 1
        client.post(f"/api/turns/{turn_id}/cancel")
        assert wait_turn(client, scene_id, turn_id)["status"] == "cancelled"


def test_turn_subscribe_last_seq_zero_replays_whole_turn(test_settings: Settings) -> None:
    steps = [
        {"text": "first turn"},
        {
            "text": "second turn preamble",
            "toolCalls": [
                {
                    "id": "ask-2",
                    "name": "ask_user",
                    "arguments": {"question": "继续吗？", "options": ["是", "否"]},
                }
            ],
        },
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        first = create_turn(client, scene_id, "first")
        assert wait_turn(client, scene_id, first)["status"] == "done"
        second = create_turn(client, scene_id, "second")
        status = ""
        for _ in range(100):
            status = client.get(f"/api/scenes/{scene_id}/turns/{second}").json()["turn"]["status"]
            if status == "waiting_user":
                break
            time.sleep(0.02)
        assert status == "waiting_user"

        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "turn.subscribe",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "payload": {"turnId": second, "lastSeq": 0},
                }
            )
            frames: list[dict[str, Any]] = []
            for _ in range(40):
                frame = websocket.receive_json()
                frames.append(frame)
                if frame["type"] == "turn.status":
                    break
            turn_frames = [frame for frame in frames if frame["ch"] == "turn"]
            assert any(frame["type"] == "turn.started" for frame in turn_frames), frames
            assert any(frame["type"] == "assistant.delta" for frame in turn_frames), frames
            assert any(frame["type"] == "turn.waiting_user" for frame in turn_frames), frames
            assert frames[-1]["type"] == "turn.status"
        client.post(f"/api/turns/{second}/cancel")

"""M3 agent tests: G1, tools, proposal chain, turn lifecycle and refusals."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.contracts import AddItemInput, AgainstWallRelation, CenterRelation
from app.agent.llm import OpenAIAdapter, ScriptedAdapter, ScriptedStep
from app.agent.placement import place_add
from app.agent.refusals import REFUSAL_SUGGESTS, make_refusal
from app.catalog.service import CatalogService
from app.contracts.ids import IdPrefix, new_id
from app.contracts.patch import ItemAddOp
from app.contracts.proposal import Proposal
from app.contracts.rules import get_rules
from app.contracts.scene import Item, Transform
from app.core.config import Settings
from app.main import create_app
from app.scene.errors import SceneError
from app.scene.fixtures import FixtureService
from app.scene.patch import apply_ops
from app.scene.service import SceneService
from app.testing.fake_llm import create_fake_openai

REPO_ROOT = Path(__file__).resolve().parents[3]
G1_SCRIPT = REPO_ROOT / "fixtures" / "llm" / "G1-01.jsonl"


def make_client(settings: Settings, steps: list[ScriptedStep | dict[str, Any]]) -> TestClient:
    return TestClient(create_app(settings, llm_adapter=ScriptedAdapter(steps)))


@pytest.fixture
def living_client(test_settings: Settings) -> Iterator[TestClient]:
    adapter = ScriptedAdapter.from_jsonl(G1_SCRIPT)
    with TestClient(create_app(test_settings, llm_adapter=adapter)) as client:
        yield client


def create_living(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/scenes", json={"fixtureId": "living-room"})
    assert response.status_code == 201, response.text
    return response.json()


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


def create_turn(client: TestClient, scene_id: str, message: str) -> str:
    response = client.post(f"/api/scenes/{scene_id}/turns", json={"message": message})
    assert response.status_code == 201, response.text
    return response.json()["turn"]["id"]


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


def test_g1_scripted_turn_dependency_chain_confirm_and_undo(
    living_client: TestClient,
) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "在客厅放一个双人沙发，靠电视墙，再配个茶几")
    turn = wait_turn(living_client, scene_id, turn_id)
    assert turn["status"] == "done", turn
    assert turn["usage"]["refusals"] == [], turn["usage"]
    assert turn["usage"]["iterations"] >= 3

    proposals = living_client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
    ).json()["proposals"]
    assert len(proposals) == 2
    assert proposals[1]["dependsOn"] == [proposals[0]["id"]]

    with living_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "scene.snapshot"
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"proposalId": proposals[0]["id"]},
            }
        )
        first = receive_until(living_client, websocket, "host.ack")
        assert any(frame["type"] == "scene.patch" for frame in first)
        assert any(
            frame["type"] == "proposal.updated"
            and frame["payload"]["proposal"]["status"] == "confirmed"
            for frame in first
        )

        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_001,
                "payload": {"proposalId": proposals[1]["id"]},
            }
        )
        second = receive_until(living_client, websocket, "host.ack")
        assert any(
            frame["type"] == "scene.patch" and frame["payload"]["patch"]["toVersion"] == 3
            for frame in second
        )

    scene = living_client.get(f"/api/scenes/{scene_id}").json()
    assert scene["version"] == 3
    kinds = {item["kindId"] for item in scene["items"]}
    assert {"sofa", "coffee_table"} <= kinds
    assert all(item["source"] == "agent" for item in scene["items"])

    undone = living_client.post(f"/api/scenes/{scene_id}/undo", json={"expectedVersion": 3})
    assert undone.status_code == 200, undone.text
    assert undone.json()["scene"]["version"] == 4
    undone_again = living_client.post(f"/api/scenes/{scene_id}/undo", json={"expectedVersion": 4})
    assert undone_again.status_code == 200, undone_again.text
    assert undone_again.json()["scene"]["items"] == []


# ---- M3 tool validation / refusal registry --------------------------------


def test_refusal_registry_has_non_empty_suggest_for_every_code() -> None:
    assert REFUSAL_SUGGESTS
    for code, suggest in REFUSAL_SUGGESTS.items():
        assert suggest.strip(), code
        assert make_refusal(code, "boom").suggest.strip(), code


def _item(kind_id: str, position: tuple[float, float, float], rotation_y: float = 0.0) -> Item:
    return Item(
        id=new_id(IdPrefix.ITEM),
        kind_id=kind_id,
        variant_id="default",
        transform=Transform(position=position, rotation_y=rotation_y),
    )


def _add(item: Item) -> ItemAddOp:
    return ItemAddOp(op="item.add", item=item)


def test_placement_l1_and_l2_refusals() -> None:
    fixtures = FixtureService()
    document = fixtures.create_document("living-room")
    room_id = document.rooms[0].id
    catalog = CatalogService().by_kind
    rules = get_rules()

    placed = place_add(
        document,
        AddItemInput(
            kind_id="sofa",
            room_id=room_id,
            relation=AgainstWallRelation(wall_label="电视墙"),
        ),
        catalog=catalog,
        rules=rules,
    )
    assert placed.ok, placed.refusals

    with pytest.raises(SceneError) as unknown_room:
        place_add(
            document,
            AddItemInput(
                kind_id="sofa",
                room_id="room_missing",
                relation=AgainstWallRelation(wall_label="电视墙"),
            ),
            catalog=catalog,
            rules=rules,
        )
    assert unknown_room.value.code == "unknown_room"

    with pytest.raises(SceneError) as unknown_kind:
        place_add(
            document,
            AddItemInput(
                kind_id="not_a_kind",
                room_id=room_id,
                relation=AgainstWallRelation(wall_label="电视墙"),
            ),
            catalog=catalog,
            rules=rules,
        )
    assert unknown_kind.value.code == "unknown_kind"

    with pytest.raises(SceneError) as variant_missing:
        place_add(
            document,
            AddItemInput(
                kind_id="sofa",
                variant_id="missing",
                room_id=room_id,
                relation=AgainstWallRelation(wall_label="电视墙"),
            ),
            catalog=catalog,
            rules=rules,
        )
    assert variant_missing.value.code == "variant_missing"

    with pytest.raises(SceneError) as missing_wall:
        place_add(
            document,
            AddItemInput(
                kind_id="sofa",
                room_id=room_id,
                relation=AgainstWallRelation(wall_label="不存在的墙"),
            ),
            catalog=catalog,
            rules=rules,
        )
    assert missing_wall.value.code == "not_found"

    # Unknown item reference (L1) is a refusal, not a crash.
    with pytest.raises(SceneError) as missing_item:
        place_add(
            document,
            AddItemInput(
                kind_id="coffee_table",
                room_id=room_id,
                relation={"type": "near_item", "itemId": "it_missing"},
            ),
            catalog=catalog,
            rules=rules,
        )
    assert missing_item.value.code == "not_found"


def test_l2_collision_out_of_room_door_window_and_walkway() -> None:
    document = FixtureService().create_document("living-room")
    catalog = CatalogService().by_kind
    rules = get_rules()

    with pytest.raises(SceneError) as outside:
        apply_ops(document, [_add(_item("plant", (10.0, 0.0, 10.0)))], catalog=catalog, rules=rules)
    assert outside.value.code == "out_of_room"

    first = apply_ops(
        document, [_add(_item("plant", (2.5, 0.0, 2.0)))], catalog=catalog, rules=rules
    )[0]
    with pytest.raises(SceneError) as collision:
        apply_ops(first, [_add(_item("plant", (2.5, 0.0, 2.0)))], catalog=catalog, rules=rules)
    assert collision.value.code == "collision"

    # The door on edge 3 opens into x 0..1.2 / z 1.8..2.7.
    with pytest.raises(SceneError) as door:
        apply_ops(
            document,
            [_add(_item("wardrobe", (0.6, 0.0, 2.25), rotation_y=math.pi / 2))],
            catalog=catalog,
            rules=rules,
        )
    assert door.value.code == "blocks_door"

    # The window on edge 1 is at x=5, z=2.25 with a 0.05 m interior strip.
    with pytest.raises(SceneError) as window:
        apply_ops(
            document,
            [_add(_item("wardrobe", (4.6, 0.0, 2.25), rotation_y=math.pi / 2))],
            catalog=catalog,
            rules=rules,
        )
    assert window.value.code == "blocks_window"

    with_gap = document.model_copy(deep=True)
    with_gap.items.append(_item("wardrobe", (2.0, 0.0, 1.0)))
    with pytest.raises(SceneError) as walkway:
        apply_ops(
            with_gap,
            [_add(_item("wardrobe", (2.0, 0.0, 1.933)))],
            catalog=catalog,
            rules=rules,
        )
    assert walkway.value.code == "walkway_too_narrow"


def test_item_too_large_on_small_wall() -> None:
    document = FixtureService().create_document("living-room")
    small = document.model_copy(deep=True)
    small.rooms[0] = small.rooms[0].model_copy(
        update={
            "polygon": [(0, 0), (1.2, 0), (1.2, 1.2), (0, 1.2)],
            "wall_labels": {"0": "电视墙"},
        }
    )
    small.openings = []
    result = place_add(
        small,
        AddItemInput(
            kind_id="sofa",
            room_id=small.rooms[0].id,
            relation=AgainstWallRelation(wall_label="电视墙"),
        ),
        catalog=CatalogService().by_kind,
        rules=get_rules(),
    )
    assert not result.ok
    assert any(refusal.code == "item_too_large" for refusal in result.refusals)
    assert all(refusal.suggest for refusal in result.refusals)


async def test_openai_adapter_streams_from_fake_service() -> None:
    fake = create_fake_openai(
        [
            {
                "text": "hello",
                "toolCalls": [
                    {"id": "call-1", "name": "query_scene", "arguments": {"includePending": True}}
                ],
                "usage": {"promptTokens": 11, "completionTokens": 7},
            }
        ]
    )
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as http_client:
        adapter = OpenAIAdapter(
            base_url="http://fake/v1",
            api_key="test-key",
            model="fake-model",
            http_client=http_client,
        )
        events = [event async for event in adapter.stream([{"role": "user", "content": "hi"}], [])]
    text = "".join(event.text for event in events if hasattr(event, "text"))
    calls = [event for event in events if event.__class__.__name__ == "ToolCall"]
    usage = [event for event in events if event.__class__.__name__ == "Usage"]
    assert text == "hello"
    assert calls and calls[0].name == "query_scene"
    assert calls[0].arguments == {"includePending": True}
    assert usage and usage[0].prompt_tokens == 11
    assert fake.state.fake_openai.requests[0]["model"] == "fake-model"


# ---- proposal chain and turn lifecycle -----------------------------------


def _g1_pending(client: TestClient, scene_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/scenes/{scene_id}/proposals", params={"status": "pending"})
    assert response.status_code == 200, response.text
    return response.json()["proposals"]


def test_reject_expires_dependants_and_confirm_fails(
    living_client: TestClient,
) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    proposals = _g1_pending(living_client, scene_id)
    assert len(proposals) == 2

    with living_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.reject",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"proposalId": proposals[0]["id"], "reason": "changed my mind"},
            }
        )
        frames = receive_until(living_client, websocket, "host.ack")
        statuses = {
            frame["payload"]["proposal"]["id"]: frame["payload"]["proposal"]["status"]
            for frame in frames
            if frame["type"] == "proposal.updated"
        }
        assert statuses[proposals[0]["id"]] == "rejected"
        assert statuses[proposals[1]["id"]] == "expired"

        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_001,
                "payload": {"proposalId": proposals[1]["id"]},
            }
        )
        error = websocket.receive_json()
        assert error["type"] == "host.error"
        assert error["payload"]["error"]["code"] == "proposal_expired"
    assert _g1_pending(living_client, scene_id) == []


def test_external_commit_expires_all_pending(living_client: TestClient) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    proposals = _g1_pending(living_client, scene_id)
    assert len(proposals) == 2

    committed = living_client.post(
        f"/api/scenes/{scene_id}/commit",
        json={
            "baseVersion": 1,
            "ops": [
                {
                    "op": "item.add",
                    "item": {
                        "id": "it_manual_plant",
                        "kindId": "plant",
                        "variantId": "default",
                        "transform": {"position": [4.0, 0.0, 3.6], "rotationY": 0},
                    },
                }
            ],
        },
    )
    assert committed.status_code == 200, committed.text
    assert _g1_pending(living_client, scene_id) == []
    expired = living_client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "expired"}
    ).json()["proposals"]
    assert len(expired) == 2
    assert all(proposal["reason"] == "scene_changed" for proposal in expired)


def test_double_confirm_is_idempotent(living_client: TestClient) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    proposals = _g1_pending(living_client, scene_id)

    with living_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()
        confirm = {
            "v": 1,
            "ch": "host",
            "type": "proposal.confirm",
            "seq": 0,
            "ts": 1_763_000_000_000,
            "payload": {"proposalId": proposals[0]["id"]},
        }
        websocket.send_json(confirm)
        first = receive_until(living_client, websocket, "host.ack")
        first_ack = first[-1]["payload"]
        assert first_ack["alreadyConfirmed"] is False

        websocket.send_json(confirm)
        second = receive_until(living_client, websocket, "host.ack")
        second_ack = second[-1]["payload"]
        assert second_ack["alreadyConfirmed"] is True
        assert second_ack["version"] == first_ack["version"]

    scene = living_client.get(f"/api/scenes/{scene_id}").json()
    assert scene["version"] == 2


def test_ws_turn_events_and_ask_user_resume(test_settings: Settings) -> None:
    steps = [
        {
            "toolCalls": [
                {
                    "id": "ask-1",
                    "name": "ask_user",
                    "arguments": {
                        "question": "Which wall?",
                        "options": ["电视墙", "东墙"],
                        "allowMultiple": False,
                    },
                }
            ]
        },
        {"text": "好的，按电视墙处理。"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()  # snapshot
            turn_id = create_turn(client, scene_id, "放一个沙发")
            waiting: dict[str, Any] = {}
            for _ in range(20):
                frame = websocket.receive_json()
                if frame.get("type") == "turn.waiting_user":
                    waiting = frame
                    break
            assert waiting, "turn.waiting_user was not emitted"
            assert waiting["payload"]["question"] == "Which wall?"
            assert waiting["payload"]["options"] == ["电视墙", "东墙"]

            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "ask_user.answer",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "turnId": turn_id,
                    "payload": {"turnId": turn_id, "answers": {"question": ["电视墙"]}},
                }
            )
            frames = receive_until(client, websocket, "turn.done")
            ack = next((frame for frame in frames if frame["type"] == "host.ack"), None)
            assert ack is not None
            done = frames[-1]
            assert done["payload"]["turn"]["status"] == "done"
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "done"


def test_tool_schema_has_provider_compatible_tools() -> None:
    from app.agent.registry import TOOL_NAMES, tool_bundle

    bundle = tool_bundle()
    assert tuple(tool.name for tool in bundle.tools) == TOOL_NAMES
    assert set(TOOL_NAMES) == {
        "query_scene",
        "search_catalog",
        "add_item",
        "add_room",
        "model_item",
        "move_item",
        "remove_item",
        "update_item",
        "ask_user",
        "auto_layout",
        "verify_layout",
    }

    def no_refs(node: Any) -> bool:
        if isinstance(node, dict):
            return "$ref" not in node and all(no_refs(value) for value in node.values())
        if isinstance(node, list):
            return all(no_refs(value) for value in node)
        return True

    for tool in bundle.tools:
        assert tool.parameters["type"] == "object"
        assert tool.parameters["additionalProperties"] is False
        assert no_refs(tool.parameters)


def test_strict_l3_rule_returns_unrealistic() -> None:
    document = FixtureService().create_document("living-room")
    result = place_add(
        document,
        AddItemInput(
            kind_id="sofa",
            room_id=document.rooms[0].id,
            relation=CenterRelation(),
        ),
        catalog=CatalogService().by_kind,
        rules=get_rules(),
    )
    assert not result.ok
    assert any(refusal.code == "unrealistic" for refusal in result.refusals)
    assert any(
        (refusal.details or {}).get("ruleId") == "sofa_no_focus" for refusal in result.refusals
    )


def test_second_turn_proposal_can_be_confirmed_while_first_turn_pending(
    test_settings: Settings,
) -> None:
    steps = [
        # Turn 1: query + sofa proposal, left pending.
        {
            "toolCalls": [
                {"id": "t1-q", "name": "query_scene", "arguments": {"includePending": True}}
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "t1-a",
                    "name": "add_item",
                    "arguments": {
                        "kindId": "sofa",
                        "roomId": "$lastRoomId",
                        "relation": {"type": "against_wall", "wallLabel": "电视墙"},
                    },
                }
            ]
        },
        {"text": "turn one done"},
        # Turn 2: reference the pending sofa and add a coffee table.
        {
            "toolCalls": [
                {"id": "t2-q", "name": "query_scene", "arguments": {"includePending": True}}
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "t2-a",
                    "name": "add_item",
                    "arguments": {
                        "kindId": "plant",
                        "roomId": "$lastRoomId",
                        "relation": {"type": "corner"},
                    },
                }
            ]
        },
        {"text": "turn two done"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        first = create_turn(client, scene_id, "第一轮：沙发")
        assert wait_turn(client, scene_id, first)["status"] == "done"
        second = create_turn(client, scene_id, "第二轮：茶几")
        assert wait_turn(client, scene_id, second)["status"] == "done"

        first_pending = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert len(first_pending) == 2
        turn2 = next(proposal for proposal in first_pending if proposal["turnId"] == second)
        assert turn2["dependsOn"] == []

        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "proposal.confirm",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "payload": {"proposalId": turn2["id"]},
                }
            )
            frames = receive_until(client, websocket, "host.ack")
            assert any(frame["type"] == "scene.patch" for frame in frames)
            expired = client.get(
                f"/api/scenes/{scene_id}/proposals", params={"status": "expired"}
            ).json()["proposals"]
            assert len(expired) == 1
        scene = client.get(f"/api/scenes/{scene_id}").json()
        assert scene["version"] == 2
        assert [item["kindId"] for item in scene["items"]] == ["plant"]


def test_manual_rest_edit_persists_and_emits_proposal_expiry(
    living_client: TestClient,
) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    assert len(_g1_pending(living_client, scene_id)) == 2

    with living_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()
        created = living_client.post(
            f"/api/scenes/{scene_id}/items",
            json={"kindId": "plant", "roomId": document["rooms"][0]["id"]},
        )
        assert created.status_code == 200, created.text
        # REST mutation has no ack: one patch plus two proposal.updated frames.
        frames = [websocket.receive_json() for _ in range(3)]
        expired_events = [
            frame
            for frame in frames
            if frame["type"] == "proposal.updated"
            and frame["payload"]["proposal"]["status"] == "expired"
        ]
        assert len(expired_events) == 2
    assert _g1_pending(living_client, scene_id) == []
    expired = living_client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "expired"}
    ).json()["proposals"]
    assert len(expired) == 2
    assert all(proposal["reason"] == "scene_changed" for proposal in expired)


def test_move_update_remove_tools_through_a_turn(test_settings: Settings) -> None:
    steps = [
        {"toolCalls": [{"id": "q", "name": "query_scene", "arguments": {"includePending": True}}]},
        {
            "toolCalls": [
                {
                    "id": "add",
                    "name": "add_item",
                    "arguments": {
                        "kindId": "plant",
                        "roomId": "$lastRoomId",
                        "relation": {"type": "corner"},
                    },
                }
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "move",
                    "name": "move_item",
                    "arguments": {
                        "itemId": "$lastItemId",
                        "relation": {"type": "center"},
                    },
                }
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "update",
                    "name": "update_item",
                    "arguments": {
                        "itemId": "$lastItemId",
                        "materialOverrides": {"pot": "ceramic"},
                    },
                }
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "remove",
                    "name": "remove_item",
                    "arguments": {"itemId": "$lastItemId"},
                }
            ]
        },
        {"text": "all four writes proposed"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        turn_id = create_turn(client, scene_id, "plant lifecycle")
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "done", turn
        assert turn["usage"]["refusals"] == [], turn["usage"]
        pending = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert [len(proposal["dependsOn"]) for proposal in pending] == [0, 1, 2, 3]

        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()
            for proposal in pending:
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
                ack = receive_until(client, websocket, "host.ack")[-1]
                assert ack["payload"]["alreadyConfirmed"] is False
        scene = client.get(f"/api/scenes/{scene_id}").json()
        assert scene["version"] == 5
        assert scene["items"] == []


def test_ttl_expiry_cascades_to_dependants(monkeypatch: pytest.MonkeyPatch) -> None:
    document = FixtureService().create_document("living-room")
    service = SceneService(document)
    now = datetime.now(UTC) - timedelta(seconds=1)
    first = Proposal(
        id=new_id(IdPrefix.PROPOSAL),
        scene_id=document.id,
        turn_id="turn_ttl",
        base_version=1,
        depends_on=[],
        ops=[],
        created_at=now,
    )
    service.add_pending(first)
    second = Proposal(
        id=new_id(IdPrefix.PROPOSAL),
        scene_id=document.id,
        turn_id="turn_ttl",
        base_version=1,
        depends_on=[first.id],
        ops=[],
        created_at=now,
    )
    service.add_pending(second)
    assert {proposal.id for proposal in service.pending_proposals} == {first.id, second.id}

    monkeypatch.setattr("app.scene.service.PROPOSAL_TTL", timedelta(seconds=0))
    expired = service.expire_due()
    assert set(expired) == {first.id, second.id}
    assert service.pending_proposals == []


def test_proposal_read_persists_ttl_expiry(
    living_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    assert len(_g1_pending(living_client, scene_id)) == 2

    monkeypatch.setattr("app.scene.service.PROPOSAL_TTL", timedelta(seconds=-1))
    assert _g1_pending(living_client, scene_id) == []
    expired = living_client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "expired"}
    ).json()["proposals"]
    assert len(expired) == 2
    assert all(proposal["reason"] == "proposal_expired" for proposal in expired)


def test_remove_item_clears_near_references(test_settings: Settings) -> None:
    steps = [
        {"toolCalls": [{"id": "q", "name": "query_scene", "arguments": {"includePending": True}}]},
        {
            "toolCalls": [
                {"id": "remove", "name": "remove_item", "arguments": {"itemId": "it_sofa_seed"}}
            ]
        },
        {"text": "removed the sofa"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        room_id = document["rooms"][0]["id"]
        seed = client.post(
            f"/api/scenes/{scene_id}/commit",
            json={
                "baseVersion": 1,
                "ops": [
                    {
                        "op": "item.add",
                        "item": {
                            "id": "it_sofa_seed",
                            "kindId": "sofa",
                            "variantId": "default",
                            "transform": {"position": [2.5, 0.0, 0.46], "rotationY": 0},
                            "constraint": {"roomId": room_id, "anchor": {"type": "wall"}},
                        },
                    },
                    {
                        "op": "item.add",
                        "item": {
                            "id": "it_table_seed",
                            "kindId": "coffee_table",
                            "variantId": "default",
                            "transform": {"position": [2.5, 0.0, 1.67], "rotationY": 0},
                            "constraint": {
                                "roomId": room_id,
                                "near": {"itemId": "it_sofa_seed", "distance": 0.45},
                            },
                        },
                    },
                ],
            },
        )
        assert seed.status_code == 200, seed.text

        turn_id = create_turn(client, scene_id, "remove the sofa")
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "done", turn
        assert turn["usage"]["refusals"] == [], turn["usage"]
        pending = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert len(pending) == 1
        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "proposal.confirm",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "payload": {"proposalId": pending[0]["id"]},
                }
            )
            receive_until(client, websocket, "host.ack")
        scene = client.get(f"/api/scenes/{scene_id}").json()
        assert all(item["id"] != "it_sofa_seed" for item in scene["items"])
        table = next(item for item in scene["items"] if item["id"] == "it_table_seed")
        assert table["constraint"]["near"] is None


def test_cross_turn_move_near_pending_item_is_refused(test_settings: Settings) -> None:
    steps = [
        # Turn 1 leaves a sofa pending.
        {
            "toolCalls": [
                {"id": "t1-q", "name": "query_scene", "arguments": {"includePending": True}}
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "t1-a",
                    "name": "add_item",
                    "arguments": {
                        "kindId": "sofa",
                        "roomId": "$lastRoomId",
                        "relation": {"type": "against_wall", "wallLabel": "电视墙"},
                    },
                }
            ]
        },
        {"text": "sofa proposed"},
        # Turn 2 tries to move the confirmed plant near the pending sofa.
        {
            "toolCalls": [
                {"id": "t2-q", "name": "query_scene", "arguments": {"includePending": True}}
            ]
        },
        {
            "toolCalls": [
                {
                    "id": "t2-m",
                    "name": "move_item",
                    "arguments": {
                        "itemId": "$firstItemId",
                        "relation": {"type": "near_item", "itemId": "$pendingItemId"},
                    },
                }
            ]
        },
        {"text": "could not move"},
    ]
    app = create_app(test_settings, llm_adapter=ScriptedAdapter(steps))
    with TestClient(app) as client:
        document = create_living(client)
        scene_id = document["id"]
        room_id = document["rooms"][0]["id"]
        seeded = client.post(
            f"/api/scenes/{scene_id}/items",
            json={
                "kindId": "plant",
                "roomId": room_id,
                "position": [4.0, 3.6],
            },
        )
        assert seeded.status_code == 200, seeded.text
        assert seeded.json()["scene"]["items"]

        first = create_turn(client, scene_id, "第一轮：沙发")
        assert wait_turn(client, scene_id, first)["status"] == "done"
        second = create_turn(client, scene_id, "第二轮：移动绿植")
        turn = wait_turn(client, scene_id, second)
        assert turn["status"] == "done", turn
        assert "proposal_expired" in turn["usage"]["refusals"]
        pending = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert len(pending) == 1
        assert pending[0]["turnId"] == first


def test_ttl_expiry_is_broadcast_on_confirm(
    living_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = create_living(living_client)
    scene_id = document["id"]
    turn_id = create_turn(living_client, scene_id, "沙发和茶几")
    wait_turn(living_client, scene_id, turn_id)
    proposals = _g1_pending(living_client, scene_id)
    assert len(proposals) == 2

    monkeypatch.setattr("app.scene.service.PROPOSAL_TTL", timedelta(seconds=-1))
    with living_client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"proposalId": proposals[0]["id"]},
            }
        )
        frames: list[dict[str, Any]] = []
        for _ in range(10):
            frame = websocket.receive_json()
            frames.append(frame)
            if frame["type"] == "host.error":
                break
        expired_events = [
            frame
            for frame in frames
            if frame["type"] == "proposal.updated"
            and frame["payload"]["proposal"]["status"] == "expired"
        ]
        assert len(expired_events) == 2
        assert frames[-1]["type"] == "host.error"
        assert frames[-1]["payload"]["error"]["code"] == "proposal_expired"

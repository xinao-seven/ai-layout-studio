"""M4 agent tests: G2 auto_layout, G3 VLM replan and capture plumbing."""

from __future__ import annotations

import io
import json
import math
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.agent.llm import ScriptedAdapter
from app.catalog.service import CatalogService
from app.contracts.rules import get_rules
from app.contracts.scene import SceneDocument
from app.core.config import Settings
from app.main import create_app
from app.scene.fixtures import FixtureService
from app.solver.circulation import room_blocking_item_ids, room_path_blocked
from app.solver.verify import verify_layout
from app.vlm.client import ScriptedVLMClient

REPO_ROOT = Path(__file__).resolve().parents[3]
G2_SCRIPT = REPO_ROOT / "fixtures" / "llm" / "G2-01.jsonl"
G3_SCRIPT = REPO_ROOT / "fixtures" / "llm" / "G3-01.jsonl"


class FakeCaptureProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def capture(
        self,
        scene_id: str,
        turn_id: str,
        legend: dict[str, Any] | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        del scene_id, turn_id, legend
        self.calls += 1
        image = Image.new("RGBA", (320, 240), (32, 32, 32, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 280, 200), outline=(220, 220, 220, 255), width=3)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), {"items": [], "openings": []}


def create_client(
    settings: Settings,
    script: Path,
    *,
    vlm: ScriptedVLMClient | None = None,
    capture: FakeCaptureProvider | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            settings,
            llm_adapter=ScriptedAdapter.from_jsonl(script),
            vlm_client=vlm,
            capture_provider=capture,
        )
    )


def create_scene(client: TestClient, fixture_id: str) -> dict[str, Any]:
    response = client.post("/api/scenes", json={"fixtureId": fixture_id})
    assert response.status_code == 201, response.text
    return response.json()


def wait_turn(
    client: TestClient, scene_id: str, turn_id: str, timeout: float = 10.0
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


def send_turn(client: TestClient, scene_id: str, message: str) -> str:
    response = client.post(f"/api/scenes/{scene_id}/turns", json={"message": message})
    assert response.status_code == 201, response.text
    return response.json()["turn"]["id"]


def pending_proposals(client: TestClient, scene_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/scenes/{scene_id}/proposals", params={"status": "pending"})
    assert response.status_code == 200, response.text
    return response.json()["proposals"]


def confirm(client: TestClient, scene_id: str, proposal_id: str) -> None:
    with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "v": 1,
                "ch": "host",
                "type": "proposal.confirm",
                "seq": 0,
                "ts": 1_763_000_000_000,
                "payload": {"proposalId": proposal_id},
            }
        )
        for _ in range(6):
            frame = websocket.receive_json()
            if frame.get("type") == "host.ack":
                return
    raise AssertionError("proposal.confirm was not acknowledged")


@pytest.fixture
def settings(test_settings: Settings) -> Settings:
    return test_settings


def test_g2_auto_layout_whole_room_proposal(settings: Settings) -> None:
    with create_client(settings, G2_SCRIPT) as client:
        document = create_scene(client, "bedroom")
        scene_id = document["id"]
        turn = wait_turn(
            client, scene_id, send_turn(client, scene_id, "帮我把卧室布置好，床朝南不挡门")
        )
        assert turn["status"] == "done", turn
        proposals = pending_proposals(client, scene_id)
        assert len(proposals) == 1
        proposal = proposals[0]
        assert len(proposal["ops"]) == 3
        assert {op["op"] for op in proposal["ops"]} == {"item.add"}

        confirm(client, scene_id, proposal["id"])
        scene = SceneDocument.model_validate(client.get(f"/api/scenes/{scene_id}").json())
        assert {item.kind_id for item in scene.items} == {
            "bed_double",
            "wardrobe",
            "nightstand",
        }
        bed = next(item for item in scene.items if item.kind_id == "bed_double")
        front = (math.sin(bed.transform.rotation_y), math.cos(bed.transform.rotation_y))
        assert front[0] * scene.north[0] + front[1] * scene.north[1] < -0.99
        verification = verify_layout(
            scene,
            CatalogService().by_kind,
            get_rules(),
            room_id=scene.rooms[0].id,
        )
        assert verification.ok
        assert verification.issues == []


def test_g3_visual_review_and_local_replan(settings: Settings) -> None:
    holder: dict[str, list[str]] = {"blockers": []}

    def vlm_response(prompt: str) -> dict[str, Any]:
        del prompt
        return {
            "verdict": "fail",
            "issues": [
                {
                    "type": "circulation_blocked",
                    "severity": "high",
                    "itemIds": holder["blockers"],
                    "openingIds": [],
                    "evidence": "The door-to-window route is blocked by a table.",
                    "suggest": "Move the table out of the main path.",
                }
            ],
        }

    capture = FakeCaptureProvider()
    vlm = ScriptedVLMClient([vlm_response])
    with create_client(settings, G3_SCRIPT, vlm=vlm, capture=capture) as client:
        document = create_scene(client, "bedroom-g3")
        scene_id = document["id"]
        loaded = SceneDocument.model_validate(client.get(f"/api/scenes/{scene_id}").json())
        blockers = room_blocking_item_ids(
            loaded,
            loaded.rooms[0],
            CatalogService().by_kind,
            get_rules(),
        )
        plant = next(item for item in loaded.items if item.kind_id == "plant")
        assert plant.id in blockers
        holder["blockers"] = [plant.id]

        turn = wait_turn(
            client, scene_id, send_turn(client, scene_id, "检查一下这个布局有没有问题")
        )
        assert turn["status"] == "done", turn
        assert capture.calls >= 1
        assert vlm.calls == 1

        proposals = pending_proposals(client, scene_id)
        assert len(proposals) == 1
        proposal = proposals[0]
        assert {op["op"] for op in proposal["ops"]} == {"item.update"}
        assert plant.id in {op["itemId"] for op in proposal["ops"]}
        confirm(client, scene_id, proposal["id"])

        final = SceneDocument.model_validate(client.get(f"/api/scenes/{scene_id}").json())
        verification = verify_layout(
            final,
            CatalogService().by_kind,
            get_rules(),
            room_id=final.rooms[0].id,
        )
        assert verification.ok, verification.model_dump(mode="json", by_alias=True)
        assert not room_path_blocked(
            final,
            final.rooms[0],
            CatalogService().by_kind,
            get_rules(),
        )


def test_verify_without_browser_reports_capture_unavailable(settings: Settings) -> None:
    with create_client(settings, G3_SCRIPT, vlm=ScriptedVLMClient([])) as client:
        document = create_scene(client, "bedroom-g3")
        scene_id = document["id"]
        turn = wait_turn(client, scene_id, send_turn(client, scene_id, "看看布局有没有问题"))
        assert turn["status"] == "done", turn
        tool_messages = [message for message in turn["messages"] if message["role"] == "tool"]
        payloads = [json.loads(message["content"]) for message in tool_messages]
        visual = [
            payload["visual"]
            for payload in payloads
            if isinstance(payload, dict) and "visual" in payload
        ]
        assert visual
        assert visual[0]["status"] == "unavailable"
        assert visual[0]["reason"] == "capture_unavailable"
        assert FixtureService().create_document("bedroom-g3").items


def test_capture_ws_round_trip_and_before_after_screenshots(settings: Settings) -> None:
    import base64

    image = Image.new("RGBA", (200, 150), (20, 24, 30, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    with create_client(settings, G2_SCRIPT) as client:
        document = create_scene(client, "bedroom")
        scene_id = document["id"]
        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()  # snapshot
            turn_id = send_turn(client, scene_id, "帮我把卧室布置好")
            requests = 0
            for _ in range(60):
                frame = websocket.receive_json()
                if frame.get("type") == "capture.request":
                    requests += 1
                    websocket.send_json(
                        {
                            "v": 1,
                            "ch": "host",
                            "type": "capture.result",
                            "seq": 0,
                            "ts": 1_763_000_000_000,
                            "requestId": frame.get("requestId"),
                            "payload": {
                                "turnId": turn_id,
                                "pngBase64": encoded,
                                "legend": {"items": [], "openings": []},
                            },
                        }
                    )
                if frame.get("type") == "turn.done":
                    break
            assert requests >= 2
        turn = wait_turn(client, scene_id, turn_id)
        assert turn["status"] == "done", turn
        assistants = [message for message in turn["messages"] if message["role"] == "assistant"]
        assert assistants[0]["screenshotBefore"]
        assert assistants[-1]["screenshotAfter"]
        url = assistants[-1]["screenshotAfter"]
        served = client.get(url)
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"


def test_capture_processing_respects_size_limits(settings: Settings) -> None:
    from app.capture.service import CaptureService

    service = CaptureService(settings)
    noise = Image.effect_noise((1800, 1400), 90).convert("RGBA")
    buffer = io.BytesIO()
    noise.save(buffer, format="PNG")
    image = service._process(buffer.getvalue(), {"items": [], "openings": []})
    assert max(image.width, image.height) <= 1536
    assert len(image.png) <= 1_000_000


def test_capture_failure_does_not_fail_turn(settings: Settings) -> None:
    class BrokenCapture:
        async def capture(
            self,
            scene_id: str,
            turn_id: str,
            legend: dict[str, Any] | None = None,
        ) -> tuple[bytes, dict[str, Any]]:
            del scene_id, turn_id, legend
            raise RuntimeError("renderer crashed")

    with create_client(settings, G2_SCRIPT, capture=BrokenCapture()) as client:
        document = create_scene(client, "bedroom")
        scene_id = document["id"]
        turn = wait_turn(client, scene_id, send_turn(client, scene_id, "帮我把卧室布置好"))
        assert turn["status"] == "done", turn
        assistants = [message for message in turn["messages"] if message["role"] == "assistant"]
        assert all(not message.get("screenshotBefore") for message in assistants)


def test_build_legend_is_room_scoped_and_stable() -> None:
    from app.vlm.service import build_legend

    document = FixtureService().create_document("two-bedroom")
    room = document.rooms[1]  # main bedroom
    legend = build_legend(document, room)
    from app.geometry.collision import item_room_id

    items = {entry["itemId"] for entry in legend["items"]}
    expected = {item.id for item in document.items if item_room_id(document, item) == room.id}
    assert items == expected
    assert all(
        item_id
        not in {item.id for item in document.items if item_room_id(document, item) != room.id}
        for item_id in items
    )
    assert [entry["label"] for entry in legend["items"]] == [
        f"A{index}" for index in range(1, len(legend["items"]) + 1)
    ]
    assert all(
        entry["openingId"]
        in {opening.id for opening in document.openings if opening.room_id == room.id}
        for entry in legend["openings"]
    )

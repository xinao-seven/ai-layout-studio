"""M3-08: G1 through the real OpenAI SDK + fake OpenAI-compatible service."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.testing.fake_llm import create_fake_openai
from tests.test_agent_m3 import create_living, create_turn, wait_turn

REPO_ROOT = Path(__file__).resolve().parents[3]
G1_SCRIPT = REPO_ROOT / "fixtures" / "llm" / "G1-01.jsonl"


def test_g1_via_real_openai_sdk_and_fake_service(test_settings: Settings) -> None:
    steps = [
        json.loads(line)
        for line in G1_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fake_app = create_fake_openai(steps)
    server = uvicorn.Server(uvicorn.Config(fake_app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "fake OpenAI server did not start"
    sockets = server.servers[0].sockets
    port = sockets[0].getsockname()[1]
    settings = Settings(
        environment="test",
        data_dir=test_settings.data_dir,
        llm_base_url=f"http://127.0.0.1:{port}/v1",
        llm_api_key="test-key",
        llm_model="fake-model",
        _env_file=None,
    )
    try:
        with TestClient(create_app(settings)) as client:
            document = create_living(client)
            scene_id = document["id"]
            turn_id = create_turn(client, scene_id, "在客厅放一个双人沙发，靠电视墙，再配个茶几")
            turn = wait_turn(client, scene_id, turn_id, timeout=15.0)
            assert turn["status"] == "done", turn
            assert turn["usage"]["refusals"] == [], turn["usage"]
            proposals = client.get(
                f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
            ).json()["proposals"]
            assert len(proposals) == 2
            assert proposals[1]["dependsOn"] == [proposals[0]["id"]]
            assert len(fake_app.state.fake_openai.requests) >= 4
    finally:
        server.should_exit = True
        thread.join(timeout=5)

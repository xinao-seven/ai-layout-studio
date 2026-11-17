"""M7 reasoning tests: caps, level mapping, streaming order and persistence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.agent.llm import OpenAIAdapter, ReasoningDelta, ScriptedAdapter, TextDelta
from app.agent.reasoning import (
    ReasoningStyle,
    build_stream_options,
    detect_reasoning_style,
)
from app.contracts.enums import ThinkingLevel
from app.core.config import Settings
from app.main import create_app
from app.testing.fake_llm import create_fake_openai
from tests.test_agent_m3 import create_living, create_turn, wait_turn

REPO_ROOT = Path(__file__).resolve().parents[3]


def _capabilities(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/agent/capabilities")
    assert response.status_code == 200, response.text
    return response.json()


def _collect_turn_frames(websocket: Any, limit: int = 200) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for _ in range(limit):
        frame = websocket.receive_json()
        if frame.get("ch") == "turn":
            frames.append(frame)
        if frame.get("type") in {"turn.done", "turn.failed", "turn.cancelled"}:
            return frames
    raise AssertionError(f"turn did not finish: {frames[-5:]}")


# ---- pure mapping ----------------------------------------------------------


def test_stream_option_mapping_per_style() -> None:
    assert build_stream_options(ReasoningStyle.OPENAI, ThinkingLevel.OFF) == build_stream_options(
        ReasoningStyle.OPENAI, None
    )
    assert not build_stream_options(ReasoningStyle.OPENAI, ThinkingLevel.OFF).reasoning_requested

    effort = build_stream_options(ReasoningStyle.OPENAI, ThinkingLevel.HIGH)
    assert effort.reasoning_effort == "high"

    deepseek = build_stream_options(ReasoningStyle.DEEPSEEK, ThinkingLevel.MEDIUM)
    assert deepseek.extra_body == {"thinking": {"type": "enabled"}}
    assert deepseek.reasoning_effort is None

    anthropic = build_stream_options(ReasoningStyle.ANTHROPIC, ThinkingLevel.LOW)
    assert anthropic.extra_body["thinking"]["budget_tokens"] == 1024
    assert anthropic.max_tokens is not None and anthropic.max_tokens > 1024

    qwen = build_stream_options(ReasoningStyle.QWEN, ThinkingLevel.LOW)
    assert qwen.extra_body == {"enable_thinking": True}

    assert not build_stream_options(ReasoningStyle.NONE, ThinkingLevel.HIGH).reasoning_requested


def test_style_detection_prefers_provider_family() -> None:
    assert detect_reasoning_style("https://api.deepseek.com", "deepseek-chat") is (
        ReasoningStyle.DEEPSEEK
    )
    assert detect_reasoning_style("https://api.openai.com/v1", "gpt-5") is ReasoningStyle.OPENAI
    assert detect_reasoning_style("https://example.invalid/v1", "local-model") is (
        ReasoningStyle.NONE
    )


# ---- capabilities endpoint -------------------------------------------------


def test_capabilities_reports_deepseek_levels(test_settings: Settings) -> None:
    settings = Settings(
        environment="test",
        data_dir=test_settings.data_dir,
        llm_base_url="https://api.deepseek.com",
        llm_api_key="test-key",
        llm_model="deepseek-chat",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        payload = _capabilities(client)
    assert payload["reasoning"] is True
    assert payload["reasoningStyle"] == "deepseek"
    assert payload["granular"] is False
    assert payload["thinkingLevels"] == ["off", "low", "medium", "high"]
    assert payload["defaultThinkingLevel"] == "off"


def test_capabilities_reports_scripted_adapter(test_settings: Settings) -> None:
    settings = Settings(
        environment="test",
        data_dir=test_settings.data_dir,
        llm_script_path=str(REPO_ROOT / "fixtures" / "llm" / "G1-01.jsonl"),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        payload = _capabilities(client)
    assert payload["model"] == "scripted"
    assert payload["reasoning"] is True
    assert payload["thinkingLevels"] == ["off", "low", "medium", "high"]


# ---- adapter ---------------------------------------------------------------


async def test_adapter_streams_reasoning_and_forwards_effort() -> None:
    fake = create_fake_openai([{"reasoning": "先看房间尺寸。", "text": "好的。"}])
    transport = httpx.ASGITransport(app=fake)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as http_client:
        adapter = OpenAIAdapter(
            base_url="http://fake/v1",
            api_key="test-key",
            model="reasoning-model",
            http_client=http_client,
            reasoning_style="openai",
        )
        events = [
            event
            async for event in adapter.stream(
                [{"role": "user", "content": "hi"}], [], thinking_level=ThinkingLevel.HIGH
            )
        ]
    reasoning = "".join(event.text for event in events if isinstance(event, ReasoningDelta))
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert reasoning == "先看房间尺寸。"
    assert text == "好的。"
    assert fake.state.fake_openai.requests[0]["reasoning_effort"] == "high"


async def test_adapter_retries_without_rejected_reasoning_param() -> None:
    calls: list[dict[str, Any]] = []

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "strict-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await request.json()
        calls.append(body)
        if "reasoning_effort" in body:
            return JSONResponse(
                {"error": {"message": "Unsupported parameter: reasoning_effort"}},
                status_code=400,
            )

        def generate() -> Iterator[str]:
            yield chunk({"role": "assistant", "content": ""})
            yield chunk({"content": "ok"})
            yield chunk({}, "stop")

        return StreamingResponse(generate(), media_type="text/event-stream")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://strict") as http_client:
        adapter = OpenAIAdapter(
            base_url="http://strict/v1",
            api_key="test-key",
            model="strict-model",
            http_client=http_client,
            reasoning_style="openai",
        )
        events = [
            event
            async for event in adapter.stream(
                [{"role": "user", "content": "hi"}], [], thinking_level=ThinkingLevel.HIGH
            )
        ]
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "ok"
    assert "reasoning_effort" in calls[0]
    assert "reasoning_effort" not in calls[1]


# ---- turn stream and persistence -------------------------------------------


def test_turn_streams_reasoning_before_text_and_persists(test_settings: Settings) -> None:
    reasoning_text = "我先确认客厅的尺寸，再决定沙发靠哪面墙，避免挡住通道。"
    steps = [
        {
            "reasoning": reasoning_text,
            "text": "好的，我会先查询场景。",
            "toolCalls": [
                {
                    "id": "call-query",
                    "name": "query_scene",
                    "arguments": {"includePending": True},
                }
            ],
        },
        {"text": "客厅尺寸已经确认。"},
    ]
    with TestClient(create_app(test_settings, llm_adapter=ScriptedAdapter(steps))) as client:
        document = create_living(client)
        scene_id = document["id"]
        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            websocket.receive_json()  # scene.snapshot
            turn_id = create_turn(client, scene_id, "在客厅放一个双人沙发")
            frames = _collect_turn_frames(websocket)

    types = [frame["type"] for frame in frames]
    first_reasoning = types.index("assistant.reasoning_delta")
    first_text = types.index("assistant.delta")
    assert first_reasoning < first_text, types

    streamed = "".join(
        str(frame["payload"].get("text", ""))
        for frame in frames
        if frame["type"] == "assistant.reasoning_delta"
    )
    assert streamed == reasoning_text

    messages = [
        frame["payload"]["reasoning"] for frame in frames if frame["type"] == "assistant.message"
    ]
    assert messages and messages[0] == reasoning_text

    turn = wait_turn(client, scene_id, turn_id)
    persisted = [message for message in turn["messages"] if message["role"] == "assistant"]
    assert persisted[0]["reasoning"] == reasoning_text


@pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
def test_create_turn_accepts_every_level(test_settings: Settings, level: str) -> None:
    with TestClient(
        create_app(test_settings, llm_adapter=ScriptedAdapter([{"text": "ok"}]))
    ) as client:
        scene_id = create_living(client)["id"]
        response = client.post(
            f"/api/scenes/{scene_id}/turns",
            json={"message": "hi", "thinkingLevel": level},
        )
        assert response.status_code == 201, response.text
        wait_turn(client, scene_id, response.json()["turn"]["id"])

"""OpenAI-compatible fake LLM service for scripted/recorded replay (M3-08).

It speaks just enough of ``POST /v1/chat/completions`` (SSE chunks) for the
real ``openai`` Python SDK to drive a turn without a network model. Steps are
the same shape as :class:`app.agent.llm.ScriptedAdapter`; ``$lastItemId`` and
``$lastRoomId`` placeholders are resolved from prior tool messages so a static
script can reference server-minted ids.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.agent.llm import ScriptedAdapter


@dataclass
class FakeStep:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class FakeOpenAIState:
    steps: list[FakeStep]
    model: str = "fake-model"
    index: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)

    def next_step(self) -> FakeStep | None:
        if self.index >= len(self.steps):
            return None
        step = self.steps[self.index]
        self.index += 1
        return step


def _coerce(step: FakeStep | dict[str, Any]) -> FakeStep:
    if isinstance(step, FakeStep):
        return step
    return FakeStep(
        text=str(step.get("text", "")),
        reasoning=str(step.get("reasoning", step.get("reasoningContent", ""))),
        tool_calls=list(step.get("toolCalls", step.get("tool_calls", []))),
        usage=dict(step.get("usage", {})),
    )


def _chunk(state: FakeOpenAIState, delta: dict[str, Any], finish_reason: str | None) -> str:
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": state.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_fake_openai(
    steps: list[FakeStep | dict[str, Any]] | None = None,
    *,
    model: str = "fake-model",
) -> FastAPI:
    state = FakeOpenAIState(steps=[_coerce(step) for step in (steps or [])], model=model)
    app = FastAPI()
    app.state.fake_openai = state

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> StreamingResponse:
        body = await request.json()
        state.requests.append(body)
        messages = body.get("messages", [])
        step = state.next_step()
        if step is None:
            payload = {
                "error": {
                    "message": "The fake OpenAI service has no further scripted responses.",
                    "type": "fake_script_exhausted",
                }
            }
            return StreamingResponse(
                iter([f"data: {json.dumps(payload)}\n\n", "data: [DONE]\n\n"]),
                media_type="text/event-stream",
                status_code=200,
            )

        def generate() -> Any:
            yield _chunk(state, {"role": "assistant", "content": ""}, None)
            for start in range(0, len(step.reasoning), 24):
                yield _chunk(
                    state,
                    {"reasoning_content": step.reasoning[start : start + 24]},
                    None,
                )
            for start in range(0, len(step.text), 24):
                yield _chunk(state, {"content": step.text[start : start + 24]}, None)
            for index, call in enumerate(step.tool_calls):
                function = call.get("function", call)
                raw_arguments = function.get("arguments", {})
                arguments = (
                    raw_arguments
                    if isinstance(raw_arguments, dict)
                    else json.loads(str(raw_arguments))
                )
                resolved = ScriptedAdapter._resolve(arguments, messages)
                yield _chunk(
                    state,
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": str(call.get("id", f"fake-call-{state.index}-{index}")),
                                "type": "function",
                                "function": {
                                    "name": str(function.get("name", "")),
                                    "arguments": json.dumps(resolved, ensure_ascii=False),
                                },
                            }
                        ]
                    },
                    None,
                )
            finish = "tool_calls" if step.tool_calls else "stop"
            yield _chunk(state, {}, finish)
            usage = {
                "prompt_tokens": int(step.usage.get("promptTokens", 0)),
                "completion_tokens": int(step.usage.get("completionTokens", 0)),
                "total_tokens": int(step.usage.get("promptTokens", 0))
                + int(step.usage.get("completionTokens", 0)),
            }
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": state.model,
                        "choices": [],
                        "usage": usage,
                    }
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app

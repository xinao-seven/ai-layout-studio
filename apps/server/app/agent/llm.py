"""Streaming LLM adapters (PRD §6.2, M3-01).

The orchestrator only consumes :data:`LLMEvent` objects, so provider-specific
streaming quirks stay inside :class:`OpenAIAdapter`. Tests use
:class:`ScriptedAdapter`; the fake OpenAI-compatible service exercises the real
SDK path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import structlog
from openai import NOT_GIVEN, AsyncOpenAI

from app.agent.reasoning import (
    build_stream_options,
    resolve_reasoning_style,
)
from app.contracts.enums import ReasoningStyle, ThinkingLevel
from app.core.config import Settings
from app.core.paths import project_roots
from app.scene.errors import SceneError

logger = structlog.get_logger(__name__)

STREAM_TIMEOUT_SECONDS = 60.0
STALL_TIMEOUT_SECONDS = 30.0

_REASONING_REJECTION_MARKERS = (
    "reasoning_effort",
    "reasoning effort",
    "thinking",
    "enable_thinking",
    "extra_body",
    "budget_tokens",
)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """Incremental reasoning/thinking content streamed by some providers."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class StreamEnd:
    finish_reason: str | None = None


LLMEvent = TextDelta | ReasoningDelta | ToolCall | Usage | StreamEnd


class LLMAdapter(Protocol):
    """Minimal provider-neutral streaming interface."""

    model: str

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[LLMEvent]: ...


def _parse_arguments(raw: str | None, tool_name: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SceneError(
            "invalid_input",
            f"Tool {tool_name!r} returned malformed JSON arguments.",
            "Retry with a valid JSON object matching the tool schema.",
            {"raw": raw[:500], "error": str(error)},
        ) from error
    if not isinstance(parsed, dict):
        raise SceneError(
            "invalid_input",
            f"Tool {tool_name!r} arguments must be a JSON object.",
            "Retry with a JSON object.",
        )
    return parsed


class OpenAIAdapter:
    """OpenAI-compatible streaming adapter (GLM / Qwen / DeepSeek / OpenAI)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: Any | None = None,
        timeout: float = STREAM_TIMEOUT_SECONDS,
        reasoning_style: str | ReasoningStyle | None = None,
    ) -> None:
        self.model = model
        self._timeout = timeout
        self._include_usage = True
        # Remembered after a provider rejects reasoning parameters so later
        # calls skip them instead of paying a failed round trip every turn.
        self._reasoning_supported = True
        self.reasoning_style = resolve_reasoning_style(base_url, model, reasoning_style)
        self._client = AsyncOpenAI(
            base_url=base_url or None,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[LLMEvent]:
        attempts = 0
        while True:
            attempts += 1
            emitted = False
            try:
                async with asyncio.timeout(self._timeout):
                    async for event in self._stream_once(messages, tools, thinking_level):
                        emitted = True
                        yield event
                return
            except TimeoutError as error:
                if emitted or attempts >= 2:
                    raise SceneError(
                        "llm_error",
                        "The model stream timed out.",
                        "Retry once; if it persists, check the LLM endpoint and model name.",
                    ) from error
                logger.info("llm.retry", reason="timeout", attempt=attempts)
            except Exception as error:
                if "stream_options" in str(error).lower():
                    # Some OpenAI-compatible providers reject the usage option.
                    self._include_usage = False
                if (
                    not emitted
                    and self._reasoning_supported
                    and self._is_reasoning_rejection(error)
                ):
                    logger.info("llm.reasoning_unsupported", style=self.reasoning_style.value)
                    self._reasoning_supported = False
                if emitted or attempts >= 2:
                    raise SceneError(
                        "llm_error",
                        f"The model request failed: {error}",
                        "Retry once; if it persists, check LLM_BASE_URL/LLM_API_KEY/LLM_MODEL.",
                    ) from error
                logger.info("llm.retry", reason=str(error), attempt=attempts)
                await asyncio.sleep(0.2)

    @staticmethod
    def _is_reasoning_rejection(error: Exception) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in _REASONING_REJECTION_MARKERS)

    async def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[LLMEvent]:
        options = (
            build_stream_options(self.reasoning_style, thinking_level)
            if self._reasoning_supported
            else None
        )
        create = cast(Any, self._client.chat.completions.create)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools if tools else NOT_GIVEN,
            "tool_choice": "auto" if tools else NOT_GIVEN,
            "temperature": 0.0,
            "stream": True,
            "stream_options": {"include_usage": True} if self._include_usage else NOT_GIVEN,
        }
        if options is not None and options.reasoning_requested:
            if options.reasoning_effort:
                kwargs["reasoning_effort"] = options.reasoning_effort
            if options.max_tokens is not None:
                kwargs["max_tokens"] = options.max_tokens
            if options.extra_body:
                kwargs["extra_body"] = options.extra_body
        stream = await create(**kwargs)
        accumulator: dict[int, dict[str, Any]] = {}
        iterator = stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), STALL_TIMEOUT_SECONDS)
            except StopAsyncIteration:
                break
            except TimeoutError as error:
                raise SceneError(
                    "llm_error",
                    "The model stream stalled for 30 s.",
                    "Retry once; check network connectivity to the LLM endpoint.",
                ) from error
            if chunk.usage is not None:
                yield Usage(
                    prompt_tokens=int(chunk.usage.prompt_tokens or 0),
                    completion_tokens=int(chunk.usage.completion_tokens or 0),
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue
            reasoning = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if isinstance(reasoning, str) and reasoning:
                yield ReasoningDelta(text=reasoning)
            if delta.content:
                yield TextDelta(text=delta.content)
            for tool_delta in delta.tool_calls or []:
                index = int(tool_delta.index or 0)
                entry = accumulator.setdefault(index, {"id": "", "name": "", "arguments": []})
                if tool_delta.id:
                    entry["id"] = tool_delta.id
                function = tool_delta.function
                if function is not None:
                    if function.name:
                        entry["name"] = function.name
                    if function.arguments:
                        entry["arguments"].append(function.arguments)
            if choice.finish_reason:
                for index in sorted(accumulator):
                    entry = accumulator[index]
                    raw = "".join(entry["arguments"])
                    yield ToolCall(
                        id=str(entry["id"] or f"call_{index}"),
                        name=str(entry["name"]),
                        arguments=_parse_arguments(raw, str(entry["name"])),
                    )
                accumulator.clear()
                yield StreamEnd(finish_reason=choice.finish_reason)
        if accumulator:
            for index in sorted(accumulator):
                entry = accumulator[index]
                raw = "".join(entry["arguments"])
                yield ToolCall(
                    id=str(entry["id"] or f"call_{index}"),
                    name=str(entry["name"]),
                    arguments=_parse_arguments(raw, str(entry["name"])),
                )
            yield StreamEnd(finish_reason="tool_calls")


@dataclass
class ScriptedStep:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class ScriptedAdapter:
    """Deterministic replay adapter used by api-ws and G1 tests."""

    def __init__(
        self, steps: Iterable[ScriptedStep | dict[str, Any]], *, model: str = "scripted"
    ) -> None:
        self.model = model
        self._steps = [self._coerce(step) for step in steps]
        self._index = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    @staticmethod
    def _coerce(step: ScriptedStep | dict[str, Any]) -> ScriptedStep:
        if isinstance(step, ScriptedStep):
            return step
        return ScriptedStep(
            text=str(step.get("text", "")),
            reasoning=str(step.get("reasoning", step.get("reasoningContent", ""))),
            tool_calls=list(step.get("toolCalls", step.get("tool_calls", []))),
            usage=dict(step.get("usage", {})),
        )

    @classmethod
    def from_jsonl(cls, path: Path, *, model: str = "scripted") -> ScriptedAdapter:
        steps = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(steps, model=model)

    @staticmethod
    def _last_item_id(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(str(message.get("content", "")))
            except json.JSONDecodeError:
                continue
            preview = payload.get("preview") if isinstance(payload, dict) else None
            if isinstance(preview, dict) and isinstance(preview.get("itemId"), str):
                return str(preview["itemId"])
        return None

    @staticmethod
    def _last_room_id(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(str(message.get("content", "")))
            except json.JSONDecodeError:
                continue
            rooms = payload.get("rooms") if isinstance(payload, dict) else None
            if isinstance(rooms, list) and rooms and isinstance(rooms[0], dict):
                room_id = rooms[0].get("id")
                if isinstance(room_id, str):
                    return room_id
        return None

    @staticmethod
    def _first_item_id(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(str(message.get("content", "")))
            except json.JSONDecodeError:
                continue
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items and isinstance(items[0], dict):
                item_id = items[0].get("id")
                if isinstance(item_id, str):
                    return item_id
        return None

    @staticmethod
    def _pending_item_id(messages: list[dict[str, Any]]) -> str | None:
        for message in reversed(messages):
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(str(message.get("content", "")))
            except json.JSONDecodeError:
                continue
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("pending") is True:
                    item_id = item.get("id")
                    if isinstance(item_id, str):
                        return item_id
        return None

    @classmethod
    def _resolve(cls, value: Any, messages: list[dict[str, Any]]) -> Any:
        if value == "$lastItemId":
            return cls._last_item_id(messages)
        if value == "$firstItemId":
            return cls._first_item_id(messages)
        if value == "$pendingItemId":
            return cls._pending_item_id(messages)
        if value == "$lastRoomId":
            return cls._last_room_id(messages)
        if isinstance(value, dict):
            return {key: cls._resolve(item, messages) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve(item, messages) for item in value]
        return value

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[LLMEvent]:
        self.seen_messages.append(json.loads(json.dumps(messages)))
        if self._index >= len(self._steps):
            raise SceneError(
                "llm_error",
                "The scripted LLM has no further responses.",
                "Add another step to the script or finish the turn earlier.",
            )
        step = self._steps[self._index]
        self._index += 1
        for start in range(0, len(step.reasoning), 24):
            yield ReasoningDelta(text=step.reasoning[start : start + 24])
            await asyncio.sleep(0)
        for start in range(0, len(step.text), 24):
            yield TextDelta(text=step.text[start : start + 24])
            await asyncio.sleep(0)
        for index, call in enumerate(step.tool_calls):
            function = call.get("function", call)
            raw_arguments = function.get("arguments", {})
            arguments = (
                raw_arguments
                if isinstance(raw_arguments, dict)
                else _parse_arguments(str(raw_arguments), str(function.get("name", "")))
            )
            yield ToolCall(
                id=str(call.get("id", f"call_{self._index}_{index}")),
                name=str(function.get("name", "")),
                arguments=self._resolve(arguments, messages),
            )
        yield Usage(
            prompt_tokens=int(step.usage.get("promptTokens", 0)),
            completion_tokens=int(step.usage.get("completionTokens", 0)),
        )
        yield StreamEnd(finish_reason="tool_calls" if step.tool_calls else "stop")


class MissingLLMAdapter:
    """Fails every request with an actionable refusal when LLM config is absent."""

    model = ""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[LLMEvent]:
        raise SceneError(
            "llm_error",
            "No LLM is configured for this server.",
            "Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env, then restart the server.",
        )
        yield StreamEnd()  # type: ignore[unreachable]  # pragma: no cover


def _resolve_script_path(raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    for root in project_roots():
        path = root / candidate
        if path.is_file():
            return path
    raise SceneError(
        "llm_error",
        f"LLM_SCRIPT_PATH {raw!r} does not exist.",
        "Point it at a JSONL script under fixtures/llm/.",
    )


def build_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_script_path:
        return ScriptedAdapter.from_jsonl(_resolve_script_path(settings.llm_script_path))
    if not settings.llm_model or not settings.llm_api_key.get_secret_value():
        return MissingLLMAdapter()
    return OpenAIAdapter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        reasoning_style=settings.llm_reasoning_style,
    )

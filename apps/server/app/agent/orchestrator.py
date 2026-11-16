"""The agent loop: stream model output, dispatch tools, feed results back."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agent.context import (
    COMPACT_AT,
    TOKEN_BUDGET,
    ContextBuilder,
    compact_tool_result,
    estimate_tokens,
)
from app.agent.llm import LLMAdapter, StreamEnd, TextDelta, ToolCall, Usage
from app.agent.refusals import make_refusal
from app.agent.registry import TOOL_BY_NAME, openai_tools
from app.agent.tools import ToolExecution, ToolExecutor
from app.contracts.enums import MessageRole, TurnStatus
from app.contracts.proposal import Proposal, TurnMessage, TurnUsage
from app.contracts.scene import SceneDocument
from app.scene.errors import SceneError

logger = structlog.get_logger(__name__)

MAX_ITERATIONS = 40
MAX_WALL_CLOCK_SECONDS = 120.0
MAX_WRITE_FAILURES = 3

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
SnapshotProvider = Callable[[], Awaitable[tuple[SceneDocument, list[Proposal], set[str]]]]
ExecutorFactory = Callable[[str], ToolExecutor]
ProposalHandler = Callable[[Proposal], Awaitable[None]]
CancelCheck = Callable[[], bool]


class TurnCancelled(Exception):
    """Raised when the user cancels the running turn."""


@dataclass
class AgentRunResult:
    messages: list[TurnMessage] = field(default_factory=list)
    usage: TurnUsage = field(default_factory=TurnUsage)
    status: TurnStatus = TurnStatus.DONE
    error: str | None = None


ProgressHandler = Callable[[AgentRunResult], Awaitable[None]]


class AgentLoop:
    """Streams one turn to completion. Turn lifecycle lives in TurnManager."""

    def __init__(
        self,
        *,
        llm: LLMAdapter,
        context_builder: ContextBuilder,
        snapshot_provider: SnapshotProvider,
        executor_factory: ExecutorFactory,
        emit: Emit,
        on_proposal: ProposalHandler,
        is_cancelled: CancelCheck,
        on_progress: ProgressHandler | None = None,
    ) -> None:
        self._llm = llm
        self._context = context_builder
        self._snapshot = snapshot_provider
        self._executor_factory = executor_factory
        self._emit = emit
        self._on_proposal = on_proposal
        self._is_cancelled = is_cancelled
        self._on_progress = on_progress
        self.result = AgentRunResult()

    async def run(
        self,
        *,
        turn_id: str,
        user_message: str,
        point_context: dict[str, Any] | None,
    ) -> AgentRunResult:
        started = time.monotonic()
        result = AgentRunResult()
        self.result = result
        document, proposals, pending_ids = await self._snapshot()
        system = self._context.system_prompt(
            document,
            proposals,
            pending_ids=pending_ids,
            point_context=point_context,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]
        result.messages.append(TurnMessage(role=MessageRole.USER, content=user_message))
        executor = self._executor_factory(turn_id)
        consecutive_write_failures = 0
        tools = openai_tools()

        for iteration in range(1, MAX_ITERATIONS + 1):
            self._check_cancel()
            if time.monotonic() - started > MAX_WALL_CLOCK_SECONDS:
                raise SceneError(
                    "budget_exceeded",
                    "The turn exceeded the 120 s wall-clock budget.",
                    "Narrow the request and retry.",
                )
            messages = self._compact(messages)
            document, proposals, pending_ids = await self._snapshot()
            messages[0] = {
                "role": "system",
                "content": self._context.system_prompt(
                    document,
                    proposals,
                    pending_ids=pending_ids,
                    point_context=point_context,
                ),
            }
            if estimate_tokens(str(messages[0]["content"])) > TOKEN_BUDGET:
                raise SceneError(
                    "budget_exceeded",
                    "The scene snapshot exceeds the 120k token context budget.",
                    "Remove pending proposals or split the scene into smaller rooms.",
                )
            result.usage.iterations = iteration

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            seen_call_ids: set[str] = set()
            finish_reason: str | None = None
            async for event in self._llm.stream(messages, tools):
                self._check_cancel()
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    await self._emit("assistant.delta", {"text": event.text})
                elif isinstance(event, ToolCall):
                    if event.id in seen_call_ids:
                        continue
                    seen_call_ids.add(event.id)
                    tool_calls.append(event)
                elif isinstance(event, Usage):
                    result.usage.tokens_in += event.prompt_tokens
                    result.usage.tokens_out += event.completion_tokens
                elif isinstance(event, StreamEnd):
                    finish_reason = event.finish_reason
            content = "".join(text_parts)
            assistant_message = TurnMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                tool_calls=[
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in tool_calls
                ]
                or None,
            )
            result.messages.append(assistant_message)
            await self._emit(
                "assistant.message",
                {
                    "content": content,
                    "toolCalls": assistant_message.tool_calls or [],
                    "finishReason": finish_reason,
                },
            )

            if not tool_calls:
                result.status = TurnStatus.DONE
                result.usage.wall_clock_ms = int((time.monotonic() - started) * 1000)
                return result

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": assistant_message.tool_calls,
                }
            )
            if self._on_progress is not None:
                await self._on_progress(result)
            builtin_writes = [
                call
                for call in tool_calls
                if TOOL_BY_NAME.get(call.name) and TOOL_BY_NAME[call.name].kind == "write"
            ]
            write_failures = 0
            for call in tool_calls:
                self._check_cancel()
                await self._emit(
                    "tool.started",
                    {"toolCallId": call.id, "name": call.name},
                )
                await self._emit(
                    "tool.args",
                    {"toolCallId": call.id, "name": call.name, "arguments": call.arguments},
                )
                try:
                    execution = await executor.execute(call.name, call.arguments)
                except SceneError as error:
                    if error.code in {"tool_timeout", "budget_exceeded"}:
                        raise
                    refusal = make_refusal(
                        error.code,
                        error.message,
                        suggest=error.suggest,
                        details=error.details,
                    )
                    execution = ToolExecution(
                        name=call.name,
                        payload={
                            "ok": False,
                            "refusals": [refusal.model_dump(by_alias=True)],
                        },
                        refusals=[refusal],
                    )
                except Exception as error:  # pragma: no cover - defensive
                    logger.exception("tool.failed", tool=call.name, error=str(error))
                    refusal = make_refusal(
                        "internal_error",
                        f"Tool {call.name!r} failed unexpectedly.",
                        details={"error": str(error)},
                    )
                    execution = ToolExecution(
                        name=call.name,
                        payload={
                            "ok": False,
                            "refusals": [refusal.model_dump(by_alias=True)],
                        },
                        refusals=[refusal],
                    )
                if execution.failed:
                    result.usage.refusals.extend(refusal.code for refusal in execution.refusals)
                    if call in builtin_writes:
                        write_failures += 1
                if execution.proposal is not None:
                    await self._on_proposal(execution.proposal)
                    await self._emit(
                        "proposal.created",
                        {"proposal": execution.proposal.model_dump(mode="json", by_alias=True)},
                    )
                payload = compact_tool_result(execution.payload)
                result.messages.append(
                    TurnMessage(
                        role=MessageRole.TOOL,
                        content=json.dumps(payload, ensure_ascii=False),
                        tool_result=payload if isinstance(payload, dict) else {"result": payload},
                    )
                )
                await self._emit(
                    "tool.result",
                    {
                        "toolCallId": call.id,
                        "name": call.name,
                        "result": payload,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )
            if builtin_writes and write_failures == len(builtin_writes):
                consecutive_write_failures += 1
            else:
                consecutive_write_failures = 0
            if self._on_progress is not None:
                await self._on_progress(result)
            if consecutive_write_failures >= MAX_WRITE_FAILURES:
                raise SceneError(
                    "budget_exceeded",
                    "Three consecutive iterations failed every write tool.",
                    "Stop retrying and explain the refusals to the user, or simplify the request.",
                    {"refusals": result.usage.refusals[-5:]},
                )
            if estimate_tokens(json.dumps(messages, ensure_ascii=False)) > TOKEN_BUDGET:
                raise SceneError(
                    "budget_exceeded",
                    "The turn exceeded the 120k token context budget.",
                    "Narrow the request or remove pending proposals first.",
                )
        raise SceneError(
            "budget_exceeded",
            f"The turn exceeded {MAX_ITERATIONS} model iterations.",
            "Narrow the request and retry.",
        )

    def _check_cancel(self) -> None:
        if self._is_cancelled():
            raise TurnCancelled()

    @staticmethod
    def _compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if estimate_tokens(json.dumps(messages, ensure_ascii=False)) <= TOKEN_BUDGET * COMPACT_AT:
            return messages
        if len(messages) <= 8:
            return messages
        tail = messages[-6:]
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        user = messages[1] if len(messages) > 1 and messages[1].get("role") == "user" else None
        if user is not None and user not in tail:
            return [messages[0], user, *tail]
        return [messages[0], *tail]

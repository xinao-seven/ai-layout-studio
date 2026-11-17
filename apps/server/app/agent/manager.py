"""Turn lifecycle: per-scene queue, cancellation, waiting_user and events."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from app.agent.context import ContextBuilder
from app.agent.llm import LLMAdapter
from app.agent.orchestrator import AgentLoop, AgentRunResult, TurnCancelled
from app.agent.tools import AskUserHandler, CatalogPublisher, ToolExecutor
from app.capture.service import CaptureService
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import MessageRole, ProposalStatus, ThinkingLevel, TurnStatus
from app.contracts.ids import IdPrefix, new_id
from app.contracts.proposal import Proposal, Turn, TurnMessage, TurnUsage
from app.contracts.rules import Rules
from app.contracts.scene import SceneDocument
from app.db.repository import SceneRepository
from app.modelgen.service import ModelGenService
from app.scene.errors import SceneError
from app.scene.registry import OverlayView, SceneRegistry
from app.solver.service import SolverService
from app.vlm.service import VLMService

logger = structlog.get_logger(__name__)

WAITING_USER_TIMEOUT_SECONDS = 600.0


class TurnSink(Protocol):
    """The gateway methods the turn manager needs (implemented by SyncGateway)."""

    async def emit_turn(
        self,
        scene_id: str,
        message_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> int: ...

    async def emit_proposal(self, scene_id: str, proposal: Proposal) -> None: ...

    async def emit_expired(self, scene_id: str, proposal_ids: list[str]) -> None: ...


class _TurnState:
    def __init__(self, turn: Turn) -> None:
        self.turn = turn
        self.point_context: dict[str, Any] | None = None
        self.thinking_level: ThinkingLevel = ThinkingLevel.OFF
        self.proposals: list[Proposal] = []
        self.task: asyncio.Task[None] | None = None
        self.cancelled = False
        self.shutdown = False
        self.answer: asyncio.Future[dict[str, list[str]]] | None = None
        self.waiting_prompt: dict[str, Any] | None = None
        self.event_seq = 0
        self.started_at = time.monotonic()
        self.error: dict[str, Any] | None = None
        self.screenshot_before: str | None = None
        self.screenshot_after: str | None = None


class TurnManager:
    """Single-process turn scheduler (PRD §6.2 / §6.4)."""

    def __init__(
        self,
        *,
        registry: SceneRegistry,
        repository: SceneRepository,
        catalog: Mapping[str, CatalogEntry],
        rules: Rules,
        llm: LLMAdapter,
        sink: TurnSink,
        solver: SolverService | None = None,
        capture: CaptureService | None = None,
        vlm: VLMService | None = None,
        modelgen: ModelGenService | None = None,
        default_thinking_level: ThinkingLevel = ThinkingLevel.OFF,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._catalog = catalog
        self._rules = rules
        self._llm = llm
        self._sink = sink
        self._solver = solver
        self._capture = capture
        self._vlm = vlm
        self._modelgen = modelgen
        self._default_thinking_level = default_thinking_level
        self._publish_catalog: CatalogPublisher | None = None
        self._context = ContextBuilder(catalog)
        self._states: dict[str, _TurnState] = {}
        self._running: dict[str, str] = {}
        self._queues: dict[str, list[str]] = defaultdict(list)
        self._scene_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._client_ids: dict[tuple[str, str], str] = {}
        self._shutting_down = False

    def update_catalog(self, catalog: Mapping[str, CatalogEntry]) -> None:
        """Refresh the catalog snapshot used by tools and the system prompt."""
        self._catalog = catalog
        self._context = ContextBuilder(catalog)

    def set_catalog_publisher(self, publisher: CatalogPublisher | None) -> None:
        """Register the hook that broadcasts a newly generated catalog entry."""
        self._publish_catalog = publisher

    # ---- queries ---------------------------------------------------------

    def live_turn(self, turn_id: str) -> Turn | None:
        state = self._states.get(turn_id)
        return state.turn.model_copy(deep=True) if state is not None else None

    def live_turns(self, scene_id: str) -> list[Turn]:
        return [
            state.turn.model_copy(deep=True)
            for state in self._states.values()
            if state.turn.scene_id == scene_id
        ]

    def active_turn_id(self, scene_id: str) -> str | None:
        return self._running.get(scene_id)

    async def get_turn(self, turn_id: str) -> Turn | None:
        live = self.live_turn(turn_id)
        if live is not None:
            return live
        return await self._repository.get_turn(turn_id)

    async def list_turns(self, scene_id: str) -> list[Turn]:
        persisted = list(await self._repository.list_turns(scene_id))
        live_by_id = {turn.id: turn for turn in self.live_turns(scene_id)}
        merged = [live_by_id.pop(turn.id, turn) for turn in persisted]
        merged.extend(live_by_id.values())
        merged.sort(key=lambda turn: turn.created_at, reverse=True)
        return merged

    def error_for(self, turn_id: str) -> dict[str, Any] | None:
        state = self._states.get(turn_id)
        return state.error if state is not None else None

    def waiting_prompt(self, turn_id: str) -> dict[str, Any] | None:
        state = self._states.get(turn_id)
        return state.waiting_prompt if state is not None else None

    # ---- commands --------------------------------------------------------

    async def create_turn(
        self,
        scene_id: str,
        message: str,
        *,
        point_context: dict[str, Any] | None = None,
        client_turn_id: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> Turn:
        await self._registry.get_document(scene_id)  # 404 for unknown scenes
        if client_turn_id is not None:
            existing = self._client_ids.get((scene_id, client_turn_id))
            if existing is not None:
                turn = await self.get_turn(existing)
                if turn is not None:
                    return turn
        turn = Turn(
            id=new_id(IdPrefix.TURN),
            scene_id=scene_id,
            status=TurnStatus.QUEUED,
            messages=[TurnMessage(role="user", content=message)],
            usage=TurnUsage(),
            created_at=datetime.now(UTC),
        )
        state = _TurnState(turn)
        state.point_context = point_context
        state.thinking_level = thinking_level or self._default_thinking_level
        state.turn.status = TurnStatus.RUNNING
        self._states[turn.id] = state
        await self._repository.create_turn(turn, message)

        start = False
        async with self._scene_locks[scene_id]:
            if scene_id not in self._running:
                self._running[scene_id] = turn.id
                start = True
            elif len(self._queues[scene_id]) < 1:
                state.turn.status = TurnStatus.QUEUED
                self._queues[scene_id].append(turn.id)
            else:
                state.turn.status = TurnStatus.FAILED
                state.turn.ended_at = datetime.now(UTC)
                await self._repository.update_turn(state.turn, error="turn_in_progress")
                self._states.pop(turn.id, None)
                raise SceneError(
                    "turn_in_progress",
                    "This scene already has one running turn and one queued turn.",
                    "Wait for the current turn or cancel it.",
                    {"sceneId": scene_id},
                )
        await self._repository.update_turn(state.turn)
        if client_turn_id is not None:
            self._client_ids[(scene_id, client_turn_id)] = turn.id
        if start:
            state.task = asyncio.create_task(self._run_turn(state))
        return state.turn.model_copy(deep=True)

    async def cancel_turn(self, turn_id: str) -> TurnStatus:
        state = self._states.get(turn_id)
        if state is None:
            turn = await self._repository.get_turn(turn_id)
            if turn is None:
                raise SceneError(
                    "not_found",
                    f"Turn {turn_id!r} does not exist.",
                    "List turns for the scene.",
                )
            return turn.status
        if state.turn.status in {
            TurnStatus.DONE,
            TurnStatus.FAILED,
            TurnStatus.CANCELLED,
        }:
            return state.turn.status
        self._forget_client_id(turn_id)
        if state.turn.status == TurnStatus.QUEUED:
            async with self._scene_locks[state.turn.scene_id]:
                queue = self._queues.get(state.turn.scene_id) or []
                if turn_id in queue:
                    queue.remove(turn_id)
            state.cancelled = True
            state.turn.status = TurnStatus.CANCELLED
            state.turn.ended_at = datetime.now(UTC)
            await self._repository.update_turn(state.turn)
            await self._emit(
                state.turn,
                "turn.cancelled",
                {"turn": self._dump_turn(state.turn)},
            )
            return TurnStatus.CANCELLED
        state.cancelled = True
        if state.answer is not None and not state.answer.done():
            state.answer.cancel()
        if state.task is not None and not state.task.done():
            state.task.cancel()
        return TurnStatus.CANCELLED

    async def answer_user(self, turn_id: str, answers: dict[str, list[str]]) -> None:
        state = self._states.get(turn_id)
        if state is None or state.answer is None or state.answer.done():
            raise SceneError(
                "invalid_input",
                "This turn is not waiting for a user answer.",
                "Check the turn status before answering.",
                {"turnId": turn_id},
            )
        state.answer.set_result(answers)

    async def restore_after_restart(self) -> None:
        failed, expired = await self._repository.recover_interrupted()
        if failed or expired:
            logger.info(
                "turns.recovered",
                failed_turns=failed,
                expired_proposals=expired,
            )

    async def shutdown(self) -> None:
        """Fail unfinished turns on graceful shutdown (same invariant as restart)."""
        self._shutting_down = True
        tasks: list[asyncio.Task[None]] = []
        for state in self._states.values():
            if state.turn.status in {
                TurnStatus.DONE,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                continue
            state.shutdown = True
            state.cancelled = True
            if state.answer is not None and not state.answer.done():
                state.answer.cancel()
            if state.task is not None and not state.task.done():
                state.task.cancel()
                tasks.append(state.task)
                continue
            # A queued turn never started; fail it directly.
            state.turn.status = TurnStatus.FAILED
            state.turn.ended_at = datetime.now(UTC)
            await self._repository.update_turn(state.turn, error="server_restart")
            await self._emit(
                state.turn,
                "turn.failed",
                {
                    "error": {
                        "code": "internal_error",
                        "message": "The server restarted before this turn ran.",
                        "suggest": "Send the request again.",
                    },
                    "turn": self._dump_turn(state.turn),
                },
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- execution -------------------------------------------------------

    async def _run_turn(self, state: _TurnState) -> None:
        turn = state.turn
        scene_id = turn.scene_id
        state.started_at = time.monotonic()
        loop: AgentLoop | None = None
        try:
            turn.status = TurnStatus.RUNNING
            await self._repository.update_turn(turn)
            await self._emit(turn, "turn.started", {"turn": self._dump_turn(turn)})
            loop = AgentLoop(
                llm=self._llm,
                context_builder=self._context,
                snapshot_provider=lambda: self._snapshot_view(scene_id),
                executor_factory=lambda turn_id: self._executor(turn_id),
                emit=lambda event_type, payload: self._emit(turn, event_type, payload),
                on_proposal=lambda proposal: self._on_proposal(turn, proposal),
                is_cancelled=lambda: state.cancelled,
                on_progress=lambda result: self._persist_progress(state, result),
                on_write_started=lambda: self._capture_before(state),
            )
            user_message = turn.messages[0].content if turn.messages else ""
            result: AgentRunResult = await loop.run(
                turn_id=turn.id,
                user_message=user_message,
                point_context=state.point_context,
                thinking_level=state.thinking_level,
            )
            turn.messages = result.messages
            await self._capture_after(state)
            self._attach_screenshots(turn, state)
            turn.usage = result.usage
            turn.usage.wall_clock_ms = int((time.monotonic() - state.started_at) * 1000)
            turn.status = result.status
            turn.ended_at = datetime.now(UTC)
            await self._repository.update_turn(turn)
            await self._emit(turn, "turn.done", {"turn": self._dump_turn(turn)})
        except (TurnCancelled, asyncio.CancelledError):
            state.cancelled = True
            self._capture_partial(state, loop)
            self._attach_screenshots(turn, state)
            await self._expire_turn_proposals(
                state, reason="server_restart" if state.shutdown else "turn_cancelled"
            )
            if state.shutdown:
                state.error = {
                    "code": "internal_error",
                    "message": "The server restarted before this turn finished.",
                    "suggest": "Send the request again.",
                }
                turn.status = TurnStatus.FAILED
                turn.ended_at = datetime.now(UTC)
                await self._repository.update_turn(turn, error="server_restart")
                await self._emit(
                    turn,
                    "turn.failed",
                    {"error": state.error, "turn": self._dump_turn(turn)},
                )
            else:
                turn.status = TurnStatus.CANCELLED
                turn.ended_at = datetime.now(UTC)
                turn.usage.wall_clock_ms = int((time.monotonic() - state.started_at) * 1000)
                await self._repository.update_turn(turn)
                await self._emit(turn, "turn.cancelled", {"turn": self._dump_turn(turn)})
        except SceneError as error:
            self._capture_partial(state, loop)
            await self._capture_after(state)
            self._attach_screenshots(turn, state)
            state.error = error.to_error_body().model_dump(mode="json", by_alias=True)
            turn.status = TurnStatus.FAILED
            turn.ended_at = datetime.now(UTC)
            turn.usage.wall_clock_ms = int((time.monotonic() - state.started_at) * 1000)
            await self._repository.update_turn(turn, error=f"{error.code}: {error.message}")
            await self._emit(
                turn,
                "turn.failed",
                {"error": state.error, "turn": self._dump_turn(turn)},
            )
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("turn.failed_unexpectedly", turn_id=turn.id, error=str(error))
            self._capture_partial(state, loop)
            await self._capture_after(state)
            self._attach_screenshots(turn, state)
            state.error = {
                "code": "internal_error",
                "message": "The turn failed unexpectedly.",
                "suggest": "Retry once; if it persists, check the server logs.",
            }
            turn.status = TurnStatus.FAILED
            turn.ended_at = datetime.now(UTC)
            await self._repository.update_turn(turn, error=str(error))
            await self._emit(turn, "turn.failed", {"error": state.error})
        finally:
            await self._release_slot(scene_id, turn.id)

    def _executor(self, turn_id: str) -> ToolExecutor:
        state = self._states[turn_id]
        return ToolExecutor(
            registry=self._registry,
            catalog=self._catalog,
            rules=self._rules,
            scene_id=state.turn.scene_id,
            turn_id=turn_id,
            ask_user=self._ask_user_handler(state),
            solver=self._solver,
            capture=self._capture,
            vlm=self._vlm,
            modelgen=self._modelgen,
            on_catalog_updated=self._publish_catalog,
        )

    def _ask_user_handler(self, state: _TurnState) -> AskUserHandler:
        async def handle(request: Any) -> dict[str, list[str]]:
            return await self._wait_for_answer(state, request)

        return handle

    async def _wait_for_answer(self, state: _TurnState, request: Any) -> dict[str, list[str]]:
        turn = state.turn
        loop = asyncio.get_running_loop()
        state.answer = loop.create_future()
        turn.status = TurnStatus.WAITING_USER
        state.waiting_prompt = {
            "question": request.question,
            "options": list(request.options),
            "allowMultiple": bool(request.allow_multiple),
        }
        await self._repository.update_turn(turn)
        await self._emit(
            turn,
            "turn.waiting_user",
            state.waiting_prompt,
        )
        try:
            answers = await asyncio.wait_for(state.answer, timeout=WAITING_USER_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise SceneError(
                "tool_timeout",
                "The user did not answer within 10 minutes.",
                "Ask a shorter question or continue with a sensible default.",
                {"turnId": turn.id},
            ) from error
        except TurnCancelled:
            raise
        finally:
            state.answer = None
            state.waiting_prompt = None
            if turn.status == TurnStatus.WAITING_USER:
                turn.status = TurnStatus.RUNNING
                await self._repository.update_turn(turn)
        return answers

    async def _persist_progress(self, state: _TurnState, result: AgentRunResult) -> None:
        """Persist partial transcript/usage so reconnect status can recover it."""
        state.turn.messages = list(result.messages)
        state.turn.usage = result.usage
        await self._repository.update_turn(state.turn)

    async def _snapshot_view(self, scene_id: str) -> tuple[SceneDocument, list[Proposal], set[str]]:
        view: OverlayView = await self._registry.overlay_view(scene_id)
        if view.expired_ids:
            await self._sink.emit_expired(scene_id, view.expired_ids)
        return view.document, view.pending, view.pending_item_ids

    async def _on_proposal(self, turn: Turn, proposal: Proposal) -> None:
        state = self._states[turn.id]
        state.proposals.append(proposal)
        await self._sink.emit_proposal(turn.scene_id, proposal)

    async def _expire_turn_proposals(self, state: _TurnState, *, reason: str) -> None:
        if not state.proposals:
            return
        expired = await self._registry.expire_turn_proposals(
            state.turn.scene_id, state.turn.id, reason=reason
        )
        expired_ids = set(expired)
        for proposal in state.proposals:
            if proposal.id not in expired_ids:
                continue
            updated = proposal.model_copy(
                update={
                    "status": ProposalStatus.EXPIRED,
                    "resolved_at": datetime.now(UTC),
                    "reason": reason,
                }
            )
            await self._sink.emit_proposal(state.turn.scene_id, updated)

    async def _emit(self, turn: Turn, event_type: str, payload: dict[str, Any]) -> None:
        state = self._states.get(turn.id)
        await self._sink.emit_turn(turn.scene_id, event_type, payload, turn_id=turn.id)
        if state is None:
            return
        state.event_seq += 1
        await self._repository.append_turn_event(turn.id, state.event_seq, event_type, payload)

    async def _release_slot(self, scene_id: str, turn_id: str) -> None:
        async with self._scene_locks[scene_id]:
            self._prune_terminal_states()
            if self._running.get(scene_id) == turn_id:
                self._running.pop(scene_id, None)
            if self._shutting_down:
                return
            queue = self._queues.get(scene_id) or []
            while queue:
                next_id = queue.pop(0)
                state = self._states.get(next_id)
                if state is None or state.cancelled:
                    continue
                state.turn.status = TurnStatus.RUNNING
                self._running[scene_id] = next_id
                state.task = asyncio.create_task(self._run_turn(state))
                return

    def _forget_client_id(self, turn_id: str) -> None:
        for key, value in list(self._client_ids.items()):
            if value == turn_id:
                self._client_ids.pop(key, None)

    def _prune_terminal_states(self, keep: int = 500) -> None:
        if len(self._states) <= keep:
            return
        terminal = sorted(
            (
                state
                for state in self._states.values()
                if state.turn.status in {TurnStatus.DONE, TurnStatus.FAILED, TurnStatus.CANCELLED}
                and (state.task is None or state.task.done())
            ),
            key=lambda state: state.turn.created_at,
        )
        for state in terminal:
            if len(self._states) <= keep:
                break
            self._states.pop(state.turn.id, None)
        while len(self._client_ids) > keep:
            self._client_ids.pop(next(iter(self._client_ids)))

    async def _capture_before(self, state: _TurnState) -> None:
        if self._capture is None:
            return
        try:
            image = await self._capture.request(
                state.turn.scene_id,
                state.turn.id,
                purpose="before",
                persistent=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - screenshots are best effort
            logger.info("screenshot.before_failed", error=str(error))
            return
        if image is not None:
            state.screenshot_before = image.url

    async def _capture_after(self, state: _TurnState) -> None:
        if self._capture is None or state.screenshot_before is None:
            return
        try:
            image = await self._capture.request(
                state.turn.scene_id,
                state.turn.id,
                purpose="after",
                persistent=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - screenshots are best effort
            logger.info("screenshot.after_failed", error=str(error))
            return
        if image is not None:
            state.screenshot_after = image.url

    @staticmethod
    def _attach_screenshots(turn: Turn, state: _TurnState) -> None:
        if not turn.messages or (
            state.screenshot_before is None and state.screenshot_after is None
        ):
            return
        assistant_indexes = [
            index
            for index, message in enumerate(turn.messages)
            if message.role == MessageRole.ASSISTANT
        ]
        if not assistant_indexes:
            return
        targets = assistant_indexes
        first = turn.messages[targets[0]]
        last = turn.messages[targets[-1]]
        first.screenshot_before = state.screenshot_before or first.screenshot_before
        last.screenshot_after = state.screenshot_after or last.screenshot_after

    @staticmethod
    def _capture_partial(state: _TurnState, loop: AgentLoop | None) -> None:
        """Preserve messages/usage accumulated before a failed or cancelled turn."""
        turn = state.turn
        if loop is not None:
            if loop.result.messages:
                turn.messages = loop.result.messages
            turn.usage = loop.result.usage
        turn.usage.wall_clock_ms = int((time.monotonic() - state.started_at) * 1000)

    @staticmethod
    def _dump_turn(turn: Turn) -> dict[str, Any]:
        return turn.model_dump(mode="json", by_alias=True)

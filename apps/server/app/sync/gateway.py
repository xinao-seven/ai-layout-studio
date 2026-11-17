"""WebSocket sync gateway: sequence numbers, state patches and resume."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import WebSocket
from pydantic import TypeAdapter, ValidationError

from app.contracts.api import ErrorBody
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import ProposalStatus
from app.contracts.patch import ScenePatch
from app.contracts.proposal import Proposal
from app.contracts.ws import (
    AskUserAnswerMessage,
    CaptureRequestPayload,
    CaptureResultMessage,
    CaptureResultPayload,
    CatalogUpdatedPayload,
    CommitMessage,
    HostMessageUnion,
    PingMessage,
    ProposalConfirmMessage,
    ProposalRejectMessage,
    RedoMessage,
    ResumeMessage,
    ScenePatchPayload,
    TurnCancelMessage,
    TurnSubscribeMessage,
    UndoMessage,
    WsChannel,
    WsEnvelope,
)
from app.scene.errors import SceneError
from app.scene.registry import SceneRegistry

if TYPE_CHECKING:
    from app.agent.manager import TurnManager

logger = structlog.get_logger(__name__)

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
BUFFER_SIZE = 500

HOST_MESSAGE_ADAPTER: TypeAdapter[Any] = TypeAdapter(HostMessageUnion)


def _now_ms() -> int:
    return int(time.time() * 1000)


class SyncGateway:
    """Single-process WebSocket fan-out for the active scene."""

    def __init__(self, registry: SceneRegistry) -> None:
        self.registry = registry
        self._seq: dict[str, dict[WsChannel, int]] = defaultdict(
            lambda: {channel: 0 for channel in WsChannel}
        )
        self._buffers: dict[str, deque[WsEnvelope]] = defaultdict(lambda: deque(maxlen=BUFFER_SIZE))
        self._turn_buffers: dict[str, deque[WsEnvelope]] = defaultdict(
            lambda: deque(maxlen=BUFFER_SIZE)
        )
        self._turn_start_seq: dict[str, int] = {}
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}
        self._capture_futures: dict[str, tuple[asyncio.Future[CaptureResultPayload], str, str]] = {}
        self._manager: TurnManager | None = None
        self._on_proposal_confirmed: Callable[[Proposal], Awaitable[None]] | None = None
        self.epoch = uuid.uuid4().hex

    def set_manager(self, manager: TurnManager) -> None:
        self._manager = manager

    def set_confirm_hook(self, hook: Callable[[Proposal], Awaitable[None]] | None) -> None:
        """Register a post-confirmation hook (used to promote generated assets)."""
        self._on_proposal_confirmed = hook

    def connected_scenes(self) -> list[str]:
        """Scene ids with at least one live host connection."""
        return [scene_id for scene_id, sockets in self._connections.items() if sockets]

    def _require_manager(self) -> TurnManager:
        if self._manager is None:
            raise SceneError(
                "internal_error",
                "The turn manager is not initialised.",
                "Restart the server.",
            )
        return self._manager

    async def emit_turn(
        self,
        scene_id: str,
        message_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> int:
        """Broadcast and buffer one turn-channel event (M3)."""
        envelope = self._next_envelope(
            scene_id,
            WsChannel.TURN,
            message_type,
            payload,
            turn_id=turn_id,
            buffer=True,
        )
        if turn_id is not None:
            self._turn_buffers[turn_id].append(envelope)
            if message_type == "turn.started":
                self._turn_start_seq[turn_id] = envelope.seq
        await self._broadcast(scene_id, envelope)
        if message_type in {"turn.done", "turn.failed", "turn.cancelled"} and turn_id is not None:
            # Terminal turns have no live subscribers that need delta replay;
            # REST history is the recovery path. Keep the shared scene buffer.
            self._turn_buffers.pop(turn_id, None)
            self._turn_start_seq.pop(turn_id, None)
        return envelope.seq

    async def emit_proposal(self, scene_id: str, proposal: Proposal) -> None:
        """Broadcast a state ``proposal.updated`` event for ghost rendering."""
        envelope = self._next_envelope(
            scene_id,
            WsChannel.STATE,
            "proposal.updated",
            {"proposal": proposal.model_dump(mode="json", by_alias=True)},
            turn_id=proposal.turn_id,
            buffer=True,
        )
        await self._broadcast(scene_id, envelope)

    async def emit_catalog(self, scene_id: str, entry: CatalogEntry) -> int:
        """Broadcast a state ``catalog.updated`` event.

        Sent *before* the ``proposal.created`` that references the new kind, so
        a host that processes frames in sequence order always has the model URL
        by the time it has to render the ghost.
        """
        envelope = self._next_envelope(
            scene_id,
            WsChannel.STATE,
            "catalog.updated",
            CatalogUpdatedPayload(entry=entry).model_dump(mode="json", by_alias=True),
            buffer=True,
        )
        await self._broadcast(scene_id, envelope)
        return envelope.seq

    def _next_envelope(
        self,
        scene_id: str,
        channel: WsChannel,
        message_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        request_id: str | None = None,
        buffer: bool = False,
    ) -> WsEnvelope:
        self._seq[scene_id][channel] += 1
        envelope = WsEnvelope(
            v=1,
            ch=channel,
            type=message_type,
            seq=self._seq[scene_id][channel],
            ts=_now_ms(),
            turn_id=turn_id,
            request_id=request_id,
            payload=payload,
        )
        if buffer:
            self._buffers[scene_id].append(envelope)
        return envelope

    async def _send(self, websocket: WebSocket, envelope: WsEnvelope) -> None:
        lock = self._send_locks.get(websocket)
        if lock is None:
            return
        async with lock:
            await websocket.send_text(envelope.model_dump_json(by_alias=True))

    async def _broadcast(self, scene_id: str, envelope: WsEnvelope) -> None:
        connections = list(self._connections.get(scene_id, set()))
        if not connections:
            return
        results = await asyncio.gather(
            *(self._send(websocket, envelope) for websocket in connections),
            return_exceptions=True,
        )
        for websocket, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                logger.info("ws.send_failed", error=str(result))
                await self.disconnect(websocket, scene_id)

    async def request_capture(
        self,
        scene_id: str,
        turn_id: str,
        *,
        timeout: float = 5.0,
        legend: dict[str, Any] | None = None,
    ) -> CaptureResultPayload | None:
        """Ask a connected host to render a labelled screenshot.

        Returns ``None`` when no browser is connected or the host does not
        answer in time, which callers surface as ``capture_unavailable``.
        """
        if not self._connections.get(scene_id):
            return None
        loop = asyncio.get_running_loop()
        request_id = f"cap_{uuid.uuid4().hex}"
        future: asyncio.Future[CaptureResultPayload] = loop.create_future()
        self._capture_futures[request_id] = (future, scene_id, turn_id)
        envelope = self._next_envelope(
            scene_id,
            WsChannel.HOST,
            "capture.request",
            CaptureRequestPayload(legend=legend).model_dump(mode="json", by_alias=True),
            turn_id=turn_id,
            request_id=request_id,
        )
        deadline = loop.time() + timeout
        try:
            await asyncio.wait_for(
                self._broadcast(scene_id, envelope),
                timeout=max(0.01, deadline - loop.time()),
            )
            return await asyncio.wait_for(future, timeout=max(0.01, deadline - loop.time()))
        except TimeoutError:
            logger.info("capture.request_timeout", scene_id=scene_id, turn_id=turn_id)
            return None
        finally:
            self._capture_futures.pop(request_id, None)

    def resolve_capture(
        self,
        request_id: str,
        scene_id: str,
        turn_id: str | None,
        payload: CaptureResultPayload,
    ) -> bool:
        entry = self._capture_futures.get(request_id)
        if entry is None:
            return False
        future, expected_scene, expected_turn = entry
        if future.done() or expected_scene != scene_id:
            return False
        if turn_id is not None and turn_id != expected_turn:
            return False
        if payload.turn_id != expected_turn:
            return False
        future.set_result(payload)
        return True

    async def connect(self, websocket: WebSocket, scene_id: str) -> None:
        """Register a connection and send the initial snapshot."""
        document = await self.registry.get_document(scene_id)
        self._connections[scene_id].add(websocket)
        self._send_locks[websocket] = asyncio.Lock()
        snapshot = self._next_envelope(
            scene_id,
            WsChannel.STATE,
            "scene.snapshot",
            {
                "scene": document.model_dump(mode="json", by_alias=True),
                "serverEpoch": self.epoch,
            },
            buffer=True,
        )
        await self._send(websocket, snapshot)

    async def _turn_status_envelope(
        self,
        scene_id: str,
        turn_id: str,
        *,
        request_id: str | None = None,
    ) -> WsEnvelope:
        manager = self._require_manager()
        turn = manager.live_turn(turn_id)
        if turn is None:
            turn = await manager.get_turn(turn_id)
        if turn is None:
            raise SceneError(
                "not_found",
                f"Turn {turn_id!r} does not exist.",
                "List turns for the scene.",
            )
        payload: dict[str, Any] = {"turn": turn.model_dump(mode="json", by_alias=True)}
        waiting = manager.waiting_prompt(turn_id)
        if waiting is not None:
            payload["waiting"] = waiting
        return self._next_envelope(
            scene_id,
            WsChannel.TURN,
            "turn.status",
            payload,
            turn_id=turn_id,
            request_id=request_id,
        )

    async def disconnect(self, websocket: WebSocket, scene_id: str) -> None:
        self._connections.get(scene_id, set()).discard(websocket)
        self._send_locks.pop(websocket, None)

    async def broadcast_patch(self, scene_id: str, patch: ScenePatch) -> None:
        payload = ScenePatchPayload(patch=patch).model_dump(mode="json", by_alias=True)
        envelope = self._next_envelope(
            scene_id,
            WsChannel.STATE,
            "scene.patch",
            payload,
            buffer=True,
        )
        await self._broadcast(scene_id, envelope)

    async def _send_error(
        self,
        websocket: WebSocket,
        scene_id: str,
        body: ErrorBody,
        *,
        request_id: str | None = None,
    ) -> None:
        envelope = self._next_envelope(
            scene_id,
            WsChannel.HOST,
            "host.error",
            {"error": body.model_dump(mode="json", by_alias=True)},
            request_id=request_id,
        )
        try:
            await self._send(websocket, envelope)
        except Exception:  # pragma: no cover - socket already gone
            await self.disconnect(websocket, scene_id)

    async def _send_ack(
        self,
        websocket: WebSocket,
        scene_id: str,
        *,
        request_id: str | None,
        message_type: str,
        version: int,
        warnings: list[Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"type": message_type, "version": version}
        if warnings:
            payload["warnings"] = [
                warning.model_dump(mode="json", by_alias=True) for warning in warnings
            ]
        if extra:
            payload.update(extra)
        envelope = self._next_envelope(
            scene_id,
            WsChannel.HOST,
            "host.ack",
            payload,
            request_id=request_id,
        )
        await self._send(websocket, envelope)

    async def _replay_or_snapshot(
        self,
        websocket: WebSocket,
        scene_id: str,
        last_state_seq: int,
        last_turn_seq: int,
    ) -> None:
        current_state = self._seq[scene_id][WsChannel.STATE]
        if last_state_seq < current_state:
            buffered = [
                envelope
                for envelope in self._buffers[scene_id]
                if envelope.ch == WsChannel.STATE
                and envelope.type != "scene.snapshot"
                and envelope.seq > last_state_seq
            ]
            if buffered and buffered[0].seq <= last_state_seq + 1:
                for envelope in buffered:
                    await self._send(websocket, envelope)
            elif buffered:
                document = await self.registry.get_document(scene_id)
                snapshot = self._next_envelope(
                    scene_id,
                    WsChannel.STATE,
                    "scene.snapshot",
                    {
                        "scene": document.model_dump(mode="json", by_alias=True),
                        "serverEpoch": self.epoch,
                    },
                    buffer=True,
                )
                await self._send(websocket, snapshot)
        active_id = self._manager.active_turn_id(scene_id) if self._manager else None
        await self._replay_turn(websocket, scene_id, active_id, last_turn_seq)
        # Send the live status after replay so a client always learns the
        # current turn state even when the buffer no longer has the gap.
        if self._manager is not None and active_id is not None:
            await self._send(
                websocket,
                await self._turn_status_envelope(scene_id, active_id),
            )

    async def _replay_turn(
        self,
        websocket: WebSocket,
        scene_id: str,
        turn_id: str | None,
        last_seq: int,
    ) -> None:
        source = self._turn_buffers[turn_id] if turn_id is not None else self._buffers[scene_id]
        buffered = [
            envelope
            for envelope in source
            if envelope.ch == WsChannel.TURN
            and envelope.seq > last_seq
            and envelope.type != "turn.status"
            and (turn_id is None or envelope.turn_id == turn_id)
        ]
        if buffered and buffered[0].seq > last_seq + 1:
            start_seq = self._turn_start_seq.get(turn_id) if turn_id is not None else None
            fresh_subscriber = last_seq == 0 or (start_seq is not None and last_seq < start_seq)
            if not fresh_subscriber:
                # A ring-buffer gap cannot be replayed as deltas; the caller
                # follows with turn.status (which includes persisted messages
                # up to the last completed iteration).
                logger.info(
                    "ws.turn_replay_gap",
                    scene_id=scene_id,
                    turn_id=turn_id,
                    last_seq=last_seq,
                    first_seq=buffered[0].seq,
                )
                return
        for envelope in buffered:
            await self._send(websocket, envelope)

    async def emit_expired(self, scene_id: str, proposal_ids: list[str]) -> None:
        for proposal_id in proposal_ids:
            proposal = await self.registry.get_proposal(proposal_id)
            if proposal is not None:
                await self.emit_proposal(scene_id, proposal)

    async def handle_message(self, websocket: WebSocket, scene_id: str, raw: str) -> None:
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            await websocket.close(code=1009, reason="message_too_large")
            return
        try:
            message = HOST_MESSAGE_ADAPTER.validate_json(raw)
        except ValidationError as error:
            await self._send_error(
                websocket,
                scene_id,
                ErrorBody(
                    code="invalid_input",
                    message="WebSocket message does not match the host contract.",
                    suggest="Check the ws.schema.json contract.",
                    details={"errors": error.errors(include_url=False, include_context=False)},
                ),
            )
            return

        request_id = getattr(message, "request_id", None)
        try:
            if isinstance(message, CommitMessage):
                outcome = await self.registry.commit(
                    scene_id,
                    message.payload.ops,
                    base_version=message.payload.base_version,
                    client_mutation_id=message.payload.client_mutation_id,
                )
                await self.broadcast_patch(scene_id, outcome.patch)
                await self.emit_expired(scene_id, outcome.expired_proposals)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="commit",
                    version=outcome.document.version,
                    warnings=outcome.warnings,
                )
            elif isinstance(message, UndoMessage):
                outcome = await self.registry.undo(
                    scene_id, expected_version=message.payload.expected_version
                )
                await self.broadcast_patch(scene_id, outcome.patch)
                await self.emit_expired(scene_id, outcome.expired_proposals)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="undo",
                    version=outcome.document.version,
                    warnings=outcome.warnings,
                )
            elif isinstance(message, RedoMessage):
                outcome = await self.registry.redo(
                    scene_id, expected_version=message.payload.expected_version
                )
                await self.broadcast_patch(scene_id, outcome.patch)
                await self.emit_expired(scene_id, outcome.expired_proposals)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="redo",
                    version=outcome.document.version,
                    warnings=outcome.warnings,
                )
            elif isinstance(message, ResumeMessage):
                await self._replay_or_snapshot(
                    websocket,
                    scene_id,
                    message.payload.last_seqs.get("state", 0),
                    message.payload.last_seqs.get("turn", 0),
                )
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="resume",
                    version=await self.registry.version(scene_id),
                )
            elif isinstance(message, ProposalConfirmMessage):
                expired_due = await self.registry.expire_due(scene_id)
                if expired_due:
                    await self.emit_expired(scene_id, expired_due)
                existing = await self.registry.get_proposal(message.payload.proposal_id)
                if existing is not None and existing.status == ProposalStatus.CONFIRMED:
                    await self.emit_proposal(scene_id, existing)
                    await self._send_ack(
                        websocket,
                        scene_id,
                        request_id=request_id,
                        message_type="proposal.confirm",
                        version=await self.registry.version(scene_id),
                        extra={"alreadyConfirmed": True},
                    )
                    return
                if existing is not None and existing.status == ProposalStatus.EXPIRED:
                    raise SceneError(
                        "proposal_expired",
                        "This proposal expired before confirmation.",
                        "Regenerate the proposal from the latest scene.",
                        {"proposalId": existing.id, "reason": existing.reason},
                    )
                outcome = await self.registry.confirm_proposal(
                    scene_id, message.payload.proposal_id
                )
                proposal = await self.registry.get_proposal(message.payload.proposal_id)
                if (
                    proposal is not None
                    and not outcome.already_confirmed
                    and self._on_proposal_confirmed is not None
                ):
                    # Promote generated assets BEFORE the patch is broadcast: the
                    # confirmation moves the files out of the temp namespace, so
                    # hosts must already hold the permanent URLs when they render
                    # the item for the first time.
                    try:
                        await self._on_proposal_confirmed(proposal)
                    except Exception as error:  # pragma: no cover - never block confirm
                        logger.warning("catalog.promote_failed", error=str(error))
                if not outcome.already_confirmed:
                    await self.broadcast_patch(scene_id, outcome.patch)
                if proposal is not None:
                    await self.emit_proposal(scene_id, proposal)
                await self.emit_expired(scene_id, outcome.expired_proposals)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="proposal.confirm",
                    version=outcome.document.version,
                    warnings=outcome.warnings,
                    extra={"alreadyConfirmed": outcome.already_confirmed},
                )
            elif isinstance(message, ProposalRejectMessage):
                expired_due = await self.registry.expire_due(scene_id)
                if expired_due:
                    await self.emit_expired(scene_id, expired_due)
                existing = await self.registry.get_proposal(message.payload.proposal_id)
                if existing is not None and existing.status == ProposalStatus.EXPIRED:
                    raise SceneError(
                        "proposal_expired",
                        "This proposal expired before rejection.",
                        "Regenerate the proposal from the latest scene.",
                        {"proposalId": existing.id, "reason": existing.reason},
                    )
                expired = await self.registry.reject_proposal(
                    scene_id, message.payload.proposal_id, message.payload.reason
                )
                proposal = await self.registry.get_proposal(message.payload.proposal_id)
                if proposal is not None:
                    await self.emit_proposal(scene_id, proposal)
                await self.emit_expired(scene_id, expired)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="proposal.reject",
                    version=await self.registry.version(scene_id),
                )
                if expired:
                    logger.info("proposal.reject_expired", scene_id=scene_id, expired=expired)
            elif isinstance(message, TurnSubscribeMessage):
                manager = self._require_manager()
                turn = await manager.get_turn(message.payload.turn_id)
                if turn is None or turn.scene_id != scene_id:
                    raise SceneError(
                        "not_found",
                        f"Turn {message.payload.turn_id!r} does not exist for this scene.",
                        "List turns for the scene.",
                    )
                await self._replay_turn(websocket, scene_id, turn.id, message.payload.last_seq)
                await self._send(
                    websocket,
                    await self._turn_status_envelope(scene_id, turn.id, request_id=request_id),
                )
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="turn.subscribe",
                    version=await self.registry.version(scene_id),
                )
            elif isinstance(message, TurnCancelMessage):
                manager = self._require_manager()
                turn = await manager.get_turn(message.payload.turn_id)
                if turn is None or turn.scene_id != scene_id:
                    raise SceneError(
                        "not_found",
                        f"Turn {message.payload.turn_id!r} does not belong to this scene.",
                        "List turns for the scene.",
                    )
                status = await manager.cancel_turn(message.payload.turn_id)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="turn.cancel",
                    version=await self.registry.version(scene_id),
                    extra={"status": status.value},
                )
            elif isinstance(message, AskUserAnswerMessage):
                manager = self._require_manager()
                turn = await manager.get_turn(message.payload.turn_id)
                if turn is None or turn.scene_id != scene_id:
                    raise SceneError(
                        "not_found",
                        f"Turn {message.payload.turn_id!r} does not belong to this scene.",
                        "List turns for the scene.",
                    )
                await manager.answer_user(message.payload.turn_id, message.payload.answers)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="ask_user.answer",
                    version=await self.registry.version(scene_id),
                )
            elif isinstance(message, CaptureResultMessage):
                if not request_id:
                    raise SceneError(
                        "invalid_input",
                        "capture.result must echo the requestId from capture.request.",
                        "Reply with the same requestId.",
                    )
                resolved = self.resolve_capture(
                    request_id,
                    scene_id,
                    message.turn_id,
                    message.payload,
                )
                if not resolved:
                    raise SceneError(
                        "invalid_input",
                        "capture.result does not match a pending capture request.",
                        "Reply to the current capture.request with its requestId and turnId.",
                        {"requestId": request_id},
                    )
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="capture.result",
                    version=await self.registry.version(scene_id),
                    extra={"accepted": resolved},
                )
            elif isinstance(message, PingMessage):
                envelope = self._next_envelope(
                    scene_id,
                    WsChannel.HOST,
                    "host.pong",
                    {"ts": message.ts},
                    request_id=request_id,
                )
                await self._send(websocket, envelope)
            else:
                await self._send_error(
                    websocket,
                    scene_id,
                    ErrorBody(
                        code="invalid_input",
                        message=f"Host message {message.type!r} is not available yet.",
                        suggest="Check the host message contract.",
                    ),
                    request_id=request_id,
                )
        except SceneError as error:
            await self._send_error(
                websocket,
                scene_id,
                error.to_error_body(),
                request_id=request_id,
            )
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("ws.handle_failed", error=str(error))
            await self._send_error(
                websocket,
                scene_id,
                ErrorBody(
                    code="internal_error",
                    message="Unexpected WebSocket error.",
                    suggest="Retry or check the server logs.",
                ),
                request_id=request_id,
            )

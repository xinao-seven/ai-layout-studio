"""WebSocket sync gateway: sequence numbers, state patches and resume."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

import structlog
from fastapi import WebSocket
from pydantic import TypeAdapter, ValidationError

from app.contracts.api import ErrorBody
from app.contracts.patch import ScenePatch
from app.contracts.ws import (
    CommitMessage,
    HostMessageUnion,
    PingMessage,
    ProposalConfirmMessage,
    ProposalRejectMessage,
    RedoMessage,
    ResumeMessage,
    ScenePatchPayload,
    UndoMessage,
    WsChannel,
    WsEnvelope,
)
from app.scene.errors import SceneError
from app.scene.registry import SceneRegistry

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
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}

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

    async def connect(self, websocket: WebSocket, scene_id: str) -> None:
        """Register a connection and send the initial snapshot."""
        document = await self.registry.get_document(scene_id)
        self._connections[scene_id].add(websocket)
        self._send_locks[websocket] = asyncio.Lock()
        snapshot = self._next_envelope(
            scene_id,
            WsChannel.STATE,
            "scene.snapshot",
            {"scene": document.model_dump(mode="json", by_alias=True)},
            buffer=True,
        )
        await self._send(websocket, snapshot)

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
    ) -> None:
        envelope = self._next_envelope(
            scene_id,
            WsChannel.HOST,
            "host.ack",
            {"type": message_type, "version": version},
            request_id=request_id,
        )
        await self._send(websocket, envelope)

    async def _replay_or_snapshot(
        self, websocket: WebSocket, scene_id: str, last_state_seq: int
    ) -> None:
        current_seq = self._seq[scene_id][WsChannel.STATE]
        if last_state_seq >= current_seq:
            return
        buffered = [
            envelope
            for envelope in self._buffers[scene_id]
            if envelope.ch == WsChannel.STATE
            and envelope.type != "scene.snapshot"
            and envelope.seq > last_state_seq
        ]
        if not buffered:
            # The connection already received a fresh snapshot on connect.
            return
        if buffered[0].seq > last_state_seq + 1:
            document = await self.registry.get_document(scene_id)
            snapshot = self._next_envelope(
                scene_id,
                WsChannel.STATE,
                "scene.snapshot",
                {"scene": document.model_dump(mode="json", by_alias=True)},
                buffer=True,
            )
            await self._send(websocket, snapshot)
            return
        for envelope in buffered:
            await self._send(websocket, envelope)

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
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="commit",
                    version=outcome.document.version,
                )
            elif isinstance(message, UndoMessage):
                outcome = await self.registry.undo(
                    scene_id, expected_version=message.payload.expected_version
                )
                await self.broadcast_patch(scene_id, outcome.patch)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="undo",
                    version=outcome.document.version,
                )
            elif isinstance(message, RedoMessage):
                outcome = await self.registry.redo(
                    scene_id, expected_version=message.payload.expected_version
                )
                await self.broadcast_patch(scene_id, outcome.patch)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="redo",
                    version=outcome.document.version,
                )
            elif isinstance(message, ResumeMessage):
                await self._replay_or_snapshot(
                    websocket,
                    scene_id,
                    message.payload.last_seqs.get("state", 0),
                )
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="resume",
                    version=await self.registry.version(scene_id),
                )
            elif isinstance(message, ProposalConfirmMessage):
                outcome = await self.registry.confirm_proposal(
                    scene_id, message.payload.proposal_id
                )
                if not outcome.already_confirmed:
                    await self.broadcast_patch(scene_id, outcome.patch)
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="proposal.confirm",
                    version=outcome.document.version,
                )
            elif isinstance(message, ProposalRejectMessage):
                expired = await self.registry.reject_proposal(
                    scene_id, message.payload.proposal_id, message.payload.reason
                )
                await self._send_ack(
                    websocket,
                    scene_id,
                    request_id=request_id,
                    message_type="proposal.reject",
                    version=await self.registry.version(scene_id),
                )
                if expired:
                    logger.info("proposal.reject_expired", scene_id=scene_id, expired=expired)
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
                        message=f"Host message {message.type!r} is not available in M1.",
                        suggest="Turn execution lands in M3.",
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

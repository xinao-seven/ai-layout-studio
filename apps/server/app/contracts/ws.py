"""WebSocket protocol contracts (PRD §6.7).

Server -> client messages use :class:`WsEnvelope`; client -> server messages use
the discriminated :data:`HostMessageUnion`. The protocol is frozen at the end
of M1.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field

from app.contracts.api import ErrorBody
from app.contracts.base import CamelModel
from app.contracts.patch import PatchOp, ScenePatch
from app.contracts.proposal import Proposal
from app.contracts.scene import SceneDocument


class WsChannel(StrEnum):
    """Logical channel multiplexed over the single WebSocket."""

    TURN = "turn"
    STATE = "state"
    HOST = "host"


class WsEnvelope(CamelModel):
    """Envelope wrapping every server -> client WebSocket message."""

    v: Literal[1] = Field(description="Protocol version.")
    ch: WsChannel
    type: str = Field(min_length=1, description="Event type, e.g. turn.started.")
    seq: int = Field(ge=0, description="Monotonic sequence number per channel.")
    ts: int = Field(description="Server unix timestamp in milliseconds.")
    turn_id: str | None = None
    request_id: str | None = None
    payload: dict[str, Any] = Field(description="Event payload.")


class SceneSnapshotPayload(CamelModel):
    scene: SceneDocument


class ScenePatchPayload(CamelModel):
    patch: ScenePatch


class ProposalUpdatedPayload(CamelModel):
    proposal: Proposal


class ServerErrorPayload(CamelModel):
    error: ErrorBody


class CommitPayload(CamelModel):
    base_version: int = Field(ge=1)
    ops: list[PatchOp]
    client_mutation_id: str | None = None


class UndoRedoPayload(CamelModel):
    expected_version: int = Field(ge=1)


class ResumePayload(CamelModel):
    last_seqs: dict[str, int] = Field(default_factory=dict)


class TurnSubscribePayload(CamelModel):
    turn_id: str


class TurnCancelPayload(CamelModel):
    turn_id: str


class ProposalConfirmPayload(CamelModel):
    proposal_id: str


class ProposalRejectPayload(CamelModel):
    proposal_id: str
    reason: str | None = None


class AskUserAnswerPayload(CamelModel):
    turn_id: str
    answers: dict[str, list[str]] = Field(default_factory=dict)


class CaptureResultPayload(CamelModel):
    turn_id: str
    png_base64: str
    legend: dict[str, Any] = Field(default_factory=dict)


class EmptyPayload(CamelModel):
    pass


class HostMessage(CamelModel):
    """Base for every client -> server WebSocket message."""

    v: Literal[1] = 1
    ch: Literal["host"] = "host"
    type: str
    seq: int = Field(default=0, ge=0)
    ts: int
    turn_id: str | None = None
    request_id: str | None = None


class CommitMessage(HostMessage):
    type: Literal["commit"]
    payload: CommitPayload


class UndoMessage(HostMessage):
    type: Literal["undo"]
    payload: UndoRedoPayload


class RedoMessage(HostMessage):
    type: Literal["redo"]
    payload: UndoRedoPayload


class ResumeMessage(HostMessage):
    type: Literal["resume"]
    payload: ResumePayload


class TurnSubscribeMessage(HostMessage):
    type: Literal["turn.subscribe"]
    payload: TurnSubscribePayload


class TurnCancelMessage(HostMessage):
    type: Literal["turn.cancel"]
    payload: TurnCancelPayload


class ProposalConfirmMessage(HostMessage):
    type: Literal["proposal.confirm"]
    payload: ProposalConfirmPayload


class ProposalRejectMessage(HostMessage):
    type: Literal["proposal.reject"]
    payload: ProposalRejectPayload


class AskUserAnswerMessage(HostMessage):
    type: Literal["ask_user.answer"]
    payload: AskUserAnswerPayload


class CaptureResultMessage(HostMessage):
    type: Literal["capture.result"]
    payload: CaptureResultPayload


class PingMessage(HostMessage):
    type: Literal["ping"]
    payload: EmptyPayload = Field(default_factory=EmptyPayload)


HostMessageUnion = Annotated[
    CommitMessage
    | UndoMessage
    | RedoMessage
    | ResumeMessage
    | TurnSubscribeMessage
    | TurnCancelMessage
    | ProposalConfirmMessage
    | ProposalRejectMessage
    | AskUserAnswerMessage
    | CaptureResultMessage
    | PingMessage,
    Field(discriminator="type"),
]

HOST_MESSAGE_TYPES = (
    "commit",
    "undo",
    "redo",
    "resume",
    "turn.subscribe",
    "turn.cancel",
    "proposal.confirm",
    "proposal.reject",
    "ask_user.answer",
    "capture.result",
    "ping",
)

WS_MODELS: tuple[type[CamelModel], ...] = (
    WsEnvelope,
    SceneSnapshotPayload,
    ScenePatchPayload,
    ProposalUpdatedPayload,
    ServerErrorPayload,
    CommitPayload,
    UndoRedoPayload,
    ResumePayload,
    TurnSubscribePayload,
    TurnCancelPayload,
    ProposalConfirmPayload,
    ProposalRejectPayload,
    AskUserAnswerPayload,
    CaptureResultPayload,
    EmptyPayload,
    HostMessage,
    CommitMessage,
    UndoMessage,
    RedoMessage,
    ResumeMessage,
    TurnSubscribeMessage,
    TurnCancelMessage,
    ProposalConfirmMessage,
    ProposalRejectMessage,
    AskUserAnswerMessage,
    CaptureResultMessage,
    PingMessage,
)

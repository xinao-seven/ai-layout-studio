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
from app.contracts.catalog import CatalogEntry
from app.contracts.patch import PatchOp, ScenePatch
from app.contracts.proposal import Proposal, Turn
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
    server_epoch: str | None = Field(
        default=None,
        description="Server process id; clients reset turn sequence state when it changes.",
    )


class ScenePatchPayload(CamelModel):
    patch: ScenePatch


class ProposalUpdatedPayload(CamelModel):
    proposal: Proposal


class CatalogUpdatedPayload(CamelModel):
    """A generated asset joined the catalog; hosts must merge it to render ghosts."""

    entry: CatalogEntry


class TurnWaitingUserPayload(CamelModel):
    question: str
    options: list[str] = Field(default_factory=list)
    allow_multiple: bool = False


class TurnStatusPayload(CamelModel):
    turn: Turn
    waiting: TurnWaitingUserPayload | None = None


class AssistantDeltaPayload(CamelModel):
    text: str


class AssistantMessagePayload(CamelModel):
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ToolEventPayload(CamelModel):
    tool_call_id: str
    name: str
    arguments: dict[str, Any] | None = None
    result: Any | None = None


class TurnFailedPayload(CamelModel):
    error: ErrorBody


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
    last_seq: int = Field(default=0, ge=0)


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


class CaptureRequestPayload(CamelModel):
    """Server -> host request for an orthographic labelled screenshot."""

    width: int = Field(default=1536, gt=0, le=4096)
    height: int = Field(default=1536, gt=0, le=4096)
    format: Literal["png"] = "png"
    max_bytes: int = Field(default=1_000_000, gt=0)
    legend: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Authoritative label/ID map for this screenshot. When present the host "
            "must label exactly these entries so the server can validate VLM ids."
        ),
    )


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
    CatalogUpdatedPayload,
    TurnStatusPayload,
    AssistantDeltaPayload,
    AssistantMessagePayload,
    ToolEventPayload,
    TurnWaitingUserPayload,
    TurnFailedPayload,
    ServerErrorPayload,
    CommitPayload,
    UndoRedoPayload,
    ResumePayload,
    TurnSubscribePayload,
    TurnCancelPayload,
    ProposalConfirmPayload,
    ProposalRejectPayload,
    AskUserAnswerPayload,
    CaptureRequestPayload,
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

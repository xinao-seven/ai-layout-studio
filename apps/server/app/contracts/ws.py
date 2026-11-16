"""WebSocket envelope contract.

The full discriminated-union protocol (turn / state / host events) is frozen
at the end of M1 (PRD §6.7). M0 only ships the envelope plus channel enum so
the contract export pipeline and front-end type generation can be exercised.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from app.contracts.base import CamelModel


class WsChannel(StrEnum):
    """Logical channel multiplexed over the single WebSocket."""

    TURN = "turn"
    STATE = "state"
    HOST = "host"


class WsEnvelope(CamelModel):
    """Envelope wrapping every WebSocket message (PRD §6.7)."""

    v: Literal[1] = Field(description="Protocol version.")
    ch: WsChannel
    type: str = Field(min_length=1, description="Event type, e.g. turn.started.")
    seq: int = Field(
        ge=0, description="Server sequence number, monotonically increasing per channel."
    )
    ts: int = Field(description="Server unix timestamp in milliseconds.")
    turn_id: str | None = None
    request_id: str | None = None
    payload: dict[str, Any] = Field(description="Event payload.")

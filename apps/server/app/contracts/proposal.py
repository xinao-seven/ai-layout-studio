"""Proposal, turn and refusal contracts (PRD §5.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.enums import MessageRole, ProposalStatus, TurnStatus
from app.contracts.patch import PatchOp


class Proposal(CamelModel):
    id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    ops: list[PatchOp]
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime
    resolved_at: datetime | None = None
    reason: str | None = None


class TurnMessage(CamelModel):
    role: MessageRole
    content: str = ""
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_result: dict[str, Any] | None = None
    screenshot_before: str | None = None
    screenshot_after: str | None = None


class TurnUsage(CamelModel):
    iterations: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    refusals: list[str] = Field(default_factory=list)
    wall_clock_ms: int = 0


class Turn(CamelModel):
    id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    status: TurnStatus = TurnStatus.QUEUED
    messages: list[TurnMessage] = Field(default_factory=list)
    usage: TurnUsage = Field(default_factory=TurnUsage)
    created_at: datetime
    ended_at: datetime | None = None


class Refusal(CamelModel):
    """Structured, stable refusal returned by operations and tools."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    suggest: str = Field(min_length=1)
    details: dict[str, Any] | None = None

"""Solver and verification result contracts (PRD §6.5, M4).

The tool *input* models live with the other tool contracts in
``app.agent.contracts`` so the OpenAI schema pipeline stays single-sourced.
These models describe structured solver/verify output used by tools, the eval
runner and the VLM closed loop.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.scene import ItemConstraint, Transform


class AutoLayoutPlacement(CamelModel):
    item_id: str = Field(min_length=1)
    kind_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    transform: Transform
    constraint: ItemConstraint | None = None
    existing: bool = Field(
        default=False,
        description="True when the solver moved an item that already existed.",
    )


class AutoLayoutUnplaced(CamelModel):
    index: int = Field(default=0, ge=0)
    kind_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    suggest: str = Field(min_length=1)


class AutoLayoutStats(CamelModel):
    candidates: int = Field(ge=0)
    backtracks: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    score: float
    partial: bool = False
    timed_out: bool = False


class AutoLayoutResult(CamelModel):
    placements: list[AutoLayoutPlacement] = Field(default_factory=list)
    unplaced: list[AutoLayoutUnplaced] = Field(default_factory=list)
    removed_item_ids: list[str] = Field(default_factory=list)
    stats: AutoLayoutStats


LayoutIssueSeverity = Literal["error", "warning", "info"]


class LayoutIssue(CamelModel):
    code: str = Field(min_length=1)
    severity: LayoutIssueSeverity
    message: str = Field(min_length=1)
    item_ids: list[str] = Field(default_factory=list)
    opening_ids: list[str] = Field(default_factory=list)
    rule_id: str | None = None


class VerifyLayoutResult(CamelModel):
    ok: bool
    issues: list[LayoutIssue] = Field(default_factory=list)


class VLMVisualIssue(CamelModel):
    """One VLM issue after server-side ID validation and geometry recheck."""

    type: Literal[
        "blocks_door",
        "walkway_narrow",
        "group_misaligned",
        "circulation_blocked",
        "window_blocked",
    ]
    severity: Literal["high", "medium", "low"]
    item_ids: list[str] = Field(default_factory=list)
    opening_ids: list[str] = Field(default_factory=list)
    confirmed: bool
    evidence: str = ""
    suggest: str = ""


class VLMVisualReview(CamelModel):
    status: Literal[
        "passed",
        "issues_found",
        "unavailable",
        "invalid_json",
        "skipped",
        "replanned",
    ]
    reason: str | None = None
    model: str = ""
    parse_attempts: int = Field(default=0, ge=0)
    rounds: int = Field(default=0, ge=0)
    confirmed: list[VLMVisualIssue] = Field(default_factory=list)
    unconfirmed_count: int = Field(default=0, ge=0)
    unknown_id_count: int = Field(default=0, ge=0)
    proposal_id: str | None = None
    rechecked: bool = False


__all__ = [
    "AutoLayoutPlacement",
    "AutoLayoutResult",
    "AutoLayoutStats",
    "AutoLayoutUnplaced",
    "LayoutIssue",
    "VerifyLayoutResult",
    "VLMVisualIssue",
    "VLMVisualReview",
]

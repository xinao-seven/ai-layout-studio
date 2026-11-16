"""Operation-runner evaluation case schema (M2-08)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agent.contracts import AutoLayoutInput
from app.contracts.base import CamelModel
from app.contracts.patch import PatchOp


class EvalItemCount(CamelModel):
    kind_id: str = Field(min_length=1)
    count: int = Field(ge=0)


class EvalExpect(CamelModel):
    """Expected outcome of a scripted operation, solver, verify or VLM case."""

    refusal: str | None = None
    version: int | None = Field(default=None, ge=1)
    items: list[EvalItemCount] = Field(default_factory=list)
    assertions: list[Literal["noCollision", "insideRoom", "doorClear"]] = Field(
        default_factory=list,
        alias="assert",
    )
    ok: bool | None = None
    issue_codes: list[str] = Field(default_factory=list)
    placement_count: int | None = Field(default=None, ge=0)
    unplaced_min: int | None = Field(default=None, ge=0)
    facing_south: bool | None = None
    max_elapsed_ms: int | None = Field(default=None, ge=0)
    visual_status: str | None = None
    rechecked: bool | None = None
    path_blocked: bool | None = None
    unconfirmed_min: int | None = Field(default=None, ge=0)
    unknown_ids_max: int | None = Field(default=None, ge=0)
    removed_min: int | None = Field(default=None, ge=0)
    kinds: list[str] = Field(default_factory=list)


class OperationEvalCase(CamelModel):
    id: str = Field(min_length=1)
    kind: Literal["operation", "solver", "verify", "vlm"] = "operation"
    tags: list[str] = Field(default_factory=list)
    scene: str = Field(min_length=1, description="Repo-relative scene JSON path.")
    room_index: int = Field(default=0, ge=0)
    operations: list[PatchOp] = Field(default_factory=list)
    layout: AutoLayoutInput | None = None
    vlm_script: list[dict[str, Any]] | None = None
    expect: EvalExpect = Field(default_factory=EvalExpect)

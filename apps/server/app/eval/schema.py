"""Operation-runner evaluation case schema (M2-08)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.patch import PatchOp


class EvalItemCount(CamelModel):
    kind_id: str = Field(min_length=1)
    count: int = Field(ge=0)


class EvalExpect(CamelModel):
    """Expected outcome of a scripted operation sequence."""

    refusal: str | None = None
    version: int | None = Field(default=None, ge=1)
    items: list[EvalItemCount] = Field(default_factory=list)
    assertions: list[Literal["noCollision", "insideRoom", "doorClear"]] = Field(
        default_factory=list,
        alias="assert",
    )


class OperationEvalCase(CamelModel):
    id: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    scene: str = Field(min_length=1, description="Repo-relative scene JSON path.")
    operations: list[PatchOp]
    expect: EvalExpect = Field(default_factory=EvalExpect)

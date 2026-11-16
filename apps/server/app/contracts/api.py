"""REST request/response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import TurnStatus
from app.contracts.patch import PatchOp, ScenePatch
from app.contracts.proposal import Proposal, Turn
from app.contracts.scene import SceneDocument, Vec2, Vec3


class SceneSummary(CamelModel):
    id: str
    name: str
    version: int = Field(ge=1)
    room_count: int = Field(ge=0)
    area_m2: float = Field(ge=0)
    updated_at: datetime


class SceneListResponse(CamelModel):
    scenes: list[SceneSummary]


class CreateSceneRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    fixture_id: str | None = None


class FixtureSummary(CamelModel):
    id: str
    name: str
    description: str = ""
    room_count: int = Field(ge=0)
    area_m2: float = Field(ge=0)


class FixtureListResponse(CamelModel):
    fixtures: list[FixtureSummary]


class CommitRequest(CamelModel):
    base_version: int = Field(ge=1)
    ops: list[PatchOp]
    client_mutation_id: str | None = None


class UndoRedoRequest(CamelModel):
    expected_version: int = Field(ge=1)


class SceneWarning(CamelModel):
    """A non-fatal change made by the server while applying a patch."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None


class SceneResult(CamelModel):
    scene: SceneDocument
    patch: ScenePatch
    warnings: list[SceneWarning] = Field(default_factory=list)


class AddItemRequest(CamelModel):
    """Manual add: the server mints the item id and resolves the variant."""

    kind_id: str = Field(min_length=1)
    variant_id: str | None = None
    room_id: str | None = None
    position: Vec2 | None = Field(
        default=None, description="Floor position [x, z]; defaults to the room centroid."
    )
    rotation_y: float = 0.0


class UpdateItemRequest(CamelModel):
    position: Vec3 | None = None
    rotation_y: float | None = None


class ImportRequest(CamelModel):
    document: SceneDocument


class CatalogResponse(CamelModel):
    entries: list[CatalogEntry]


class CreateTurnRequest(CamelModel):
    """User message that starts an agent turn (PRD §6.2)."""

    message: str = Field(min_length=1, max_length=4000)
    point_context: dict[str, Any] | None = None
    client_turn_id: str | None = None


class TurnListResponse(CamelModel):
    turns: list[Turn]


class TurnResponse(CamelModel):
    turn: Turn


class TurnCancelResponse(CamelModel):
    ok: bool = True
    status: TurnStatus


class ProposalListResponse(CamelModel):
    proposals: list[Proposal]


class ErrorBody(CamelModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    suggest: str | None = None
    details: dict[str, Any] | None = None


class ErrorEnvelope(CamelModel):
    error: ErrorBody

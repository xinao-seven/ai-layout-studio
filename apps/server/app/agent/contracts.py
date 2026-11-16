"""Pydantic input contracts for the seven M3 agent tools.

The same models generate the OpenAI tool schemas (via ``model_json_schema``)
and perform L0 validation, so there is no hand-written second schema (PRD
§6.1/§6.3).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts.base import CamelModel
from app.contracts.enums import CatalogCategory, FacingDirection
from app.contracts.scene import Clearance


class AgainstWallRelation(CamelModel):
    """Place the item's back against a wall (optionally a labelled wall)."""

    type: Literal["against_wall"] = "against_wall"
    wall_id: str | None = Field(
        default=None, description="Derived wall id from query_scene, e.g. room_x:e0."
    )
    wall_label: str | None = Field(
        default=None, description="Human wall label from the scene, e.g. 电视墙."
    )


class NearWindowRelation(CamelModel):
    """Place the item near a window opening."""

    type: Literal["near_window"] = "near_window"
    opening_id: str | None = None


class NearItemRelation(CamelModel):
    """Place the item beside another item (may reference a pending item)."""

    type: Literal["near_item"] = "near_item"
    item_id: str = Field(min_length=1)
    distance: float | None = Field(
        default=None,
        ge=0,
        le=2.0,
        description="Gap in meters between the two footprints; defaults to 0.45.",
    )


class CornerRelation(CamelModel):
    """Place the item in a room corner."""

    type: Literal["corner"] = "corner"


class CenterRelation(CamelModel):
    """Place the item near the room centroid."""

    type: Literal["center"] = "center"


Relation = Annotated[
    AgainstWallRelation | NearWindowRelation | NearItemRelation | CornerRelation | CenterRelation,
    Field(discriminator="type"),
]


class FacingTowardInput(CamelModel):
    """Front (+Z) points at a door, window or item."""

    toward: Literal["door", "window", "item"]
    id: str | None = None


FacingInput = FacingDirection | FacingTowardInput


class QuerySceneInput(CamelModel):
    room_id: str | None = Field(default=None, description="Limit rooms/items/openings to one room.")
    item_ids: list[str] | None = Field(
        default=None, description="Only return these item ids (plus room context)."
    )
    include_pending: bool = Field(
        default=True, description="Include pending proposal items marked pending=true."
    )


class SearchCatalogInput(CamelModel):
    query: str | None = Field(default=None, description="Free-text match on kind id, name or tags.")
    category: CatalogCategory | None = None
    min_width_m: float | None = Field(default=None, ge=0)
    max_width_m: float | None = Field(default=None, ge=0)
    min_depth_m: float | None = Field(default=None, ge=0)
    max_depth_m: float | None = Field(default=None, ge=0)
    min_height_m: float | None = Field(default=None, ge=0)
    max_height_m: float | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _check_ranges(self) -> SearchCatalogInput:
        for low_name, high_name in (
            ("min_width_m", "max_width_m"),
            ("min_depth_m", "max_depth_m"),
            ("min_height_m", "max_height_m"),
        ):
            low = getattr(self, low_name)
            high = getattr(self, high_name)
            if low is not None and high is not None and low > high:
                raise ValueError(f"{low_name} must not exceed {high_name}")
        return self


class AddItemInput(CamelModel):
    kind_id: str = Field(min_length=1)
    variant_id: str | None = None
    room_id: str = Field(min_length=1)
    relation: Relation
    facing: FacingInput | None = None
    clearance: Clearance | None = None


class MoveOffset(CamelModel):
    dx: float = Field(description="Translation along world +X in meters.")
    dz: float = Field(description="Translation along world +Z in meters.")


class MoveItemInput(CamelModel):
    item_id: str = Field(min_length=1)
    relation: Relation | None = None
    offset: MoveOffset | None = None
    facing: FacingInput | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> MoveItemInput:
        if (self.relation is None) == (self.offset is None):
            raise ValueError("Provide exactly one of relation or offset.")
        return self


class RemoveItemInput(CamelModel):
    item_id: str = Field(min_length=1)


class UpdateItemInput(CamelModel):
    item_id: str = Field(min_length=1)
    variant_id: str | None = None
    material_overrides: dict[str, str] | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> UpdateItemInput:
        if self.variant_id is None and self.material_overrides is None:
            raise ValueError("Provide variantId or materialOverrides.")
        return self


class AskUserInput(CamelModel):
    question: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(default_factory=list, max_length=8)
    allow_multiple: bool = False

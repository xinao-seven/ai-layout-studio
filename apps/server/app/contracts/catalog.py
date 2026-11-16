"""Furniture catalog contracts (PRD §5.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.enums import CatalogCategory, FacingDirection
from app.contracts.scene import Clearance, Vec3


class CatalogVariant(CamelModel):
    id: str = Field(min_length=1)
    size_m: Vec3
    model_path: str
    thumbnail_path: str


class CatalogRules(CamelModel):
    must_against_wall: bool = False
    preferred_wall: FacingDirection | None = None
    group_with: list[str] = Field(default_factory=list)
    anchor_priority: int = 0
    allow_overlap: bool = False
    room_types: list[str] = Field(default_factory=list)


class CatalogCollision(CamelModel):
    type: Literal["obb"] = "obb"


class CatalogLicense(CamelModel):
    name: str
    url: str


class CatalogEntry(CamelModel):
    kind_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: CatalogCategory = CatalogCategory.OTHER
    tags: list[str] = Field(default_factory=list)
    placement: Literal["floor"] = "floor"
    default_variant_id: str
    variants: list[CatalogVariant] = Field(min_length=1)
    front_axis: Literal["+Z"] = "+Z"
    pivot: Literal["bottom-center"] = "bottom-center"
    collision: CatalogCollision = Field(default_factory=CatalogCollision)
    clearance: Clearance = Field(default_factory=Clearance)
    allowed_rotations: list[float] = Field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    rules: CatalogRules | None = None
    license: CatalogLicense | None = None

    def variant(self, variant_id: str) -> CatalogVariant:
        for candidate in self.variants:
            if candidate.id == variant_id:
                return candidate
        raise ValueError(f"unknown variant {variant_id!r} for kind {self.kind_id!r}")

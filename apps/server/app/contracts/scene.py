"""Scene document contracts (PRD §5.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.contracts.base import CamelModel
from app.contracts.enums import (
    AnchorType,
    DoorHinge,
    DoorSwing,
    FacingDirection,
    FacingToward,
    ItemSource,
    OpeningType,
    OpensTo,
    RoomType,
)

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
PositiveFloat = Annotated[float, Field(gt=0)]


class SceneDefaults(CamelModel):
    """Default structural values for a scene."""

    wall_height: PositiveFloat = 2.8
    wall_thickness: PositiveFloat = 0.12
    wall_material: str = "default"


class EnvironmentSettings(CamelModel):
    """Geographic anchor for the daylight layer (PRD FR-22).

    Latitude/longitude feed the solar-position model; the sun direction is
    expressed relative to ``SceneDocument.north`` so rotating the compass
    rotates the shadows with it. ``None`` keeps the scene on studio lighting.
    """

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class Room(CamelModel):
    """Room whose polygon is the wall centerline (CCW)."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: RoomType = RoomType.OTHER
    polygon: list[Vec2] = Field(min_length=3)
    wall_labels: dict[str, str] | None = None
    wall_height: PositiveFloat | None = None
    wall_thickness: PositiveFloat | None = None
    floor_material: str = "default"


class DoorSpec(CamelModel):
    """Door geometry and swing metadata (PRD §5.1)."""

    swing: DoorSwing = DoorSwing.SINGLE
    hinge: DoorHinge = DoorHinge.START
    opens_to: OpensTo = OpensTo.IN


class Opening(CamelModel):
    """Door or window anchored to a room polygon edge."""

    id: str = Field(min_length=1)
    type: OpeningType
    room_id: str = Field(min_length=1)
    edge_index: int = Field(ge=0)
    offset_from: Literal["start"] = "start"
    offset: float = Field(ge=0)
    width: PositiveFloat
    height: PositiveFloat
    sill_height: float | None = Field(default=None, ge=0)
    door: DoorSpec | None = None
    style: str | None = None


class Transform(CamelModel):
    """Item placement. Rotation is radians around +Y; front axis is +Z."""

    position: Vec3
    rotation_y: float
    scale: float = Field(default=1.0, gt=0)


class Anchor(CamelModel):
    type: AnchorType
    wall_id: str | None = None
    wall_label: str | None = None
    opening_id: str | None = None


class Near(CamelModel):
    item_id: str | None = None
    opening_id: str | None = None
    distance: float | None = Field(default=None, gt=0)


class FacingTowardSpec(CamelModel):
    toward: FacingToward
    id: str | None = None


class Clearance(CamelModel):
    front: float | None = Field(default=None, ge=0)
    sides: float | None = Field(default=None, ge=0)
    back: float | None = Field(default=None, ge=0)


class ItemConstraint(CamelModel):
    """Preserved agent intent so placements stay explainable and re-solvable."""

    room_id: str | None = None
    anchor: Anchor | None = None
    near: Near | None = None
    facing: FacingDirection | FacingTowardSpec | None = None
    clearance: Clearance | None = None
    group: str | None = None


class Item(CamelModel):
    id: str = Field(min_length=1)
    kind_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    transform: Transform
    constraint: ItemConstraint | None = None
    material_overrides: dict[str, str] | None = None
    source: ItemSource = ItemSource.MANUAL


class SceneDocument(CamelModel):
    """Confirmed scene document; the single source of truth on the server."""

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    units: Literal["metric"] = "metric"
    north: Vec2 = (0.0, -1.0)
    environment: EnvironmentSettings | None = None
    defaults: SceneDefaults
    rooms: list[Room]
    openings: list[Opening]
    items: list[Item]
    created_at: datetime
    updated_at: datetime

    @field_validator("north")
    @classmethod
    def _normalise_north(cls, value: Vec2) -> Vec2:
        x, z = value
        length = (x * x + z * z) ** 0.5
        if length <= 0:
            raise ValueError("north must be a non-zero vector")
        return (x / length, z / length)


class WallSegment(CamelModel):
    """Derived wall segment; never persisted (PRD §5.2)."""

    id: str
    room_id: str
    edge_index: int
    a: Vec2
    b: Vec2
    normal: Vec2
    length: float
    thickness: float
    height: float

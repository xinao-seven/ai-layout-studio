"""Semantic patch operations (PRD §5.4).

Operations are the only way scene state changes. Update ops use typed partial
payloads (identity fields are not mutable); the server fills full field
snapshots into returned patches and generates inverses for undo.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from app.contracts.base import CamelModel
from app.contracts.enums import Actor, ItemSource, OpeningType, RoomType
from app.contracts.scene import DoorSpec, Item, ItemConstraint, Opening, Room, Vec2, Vec3


class TransformUpdate(CamelModel):
    position: Vec3 | None = None
    rotation_y: float | None = None
    scale: float | None = Field(default=None, gt=0)


class ItemUpdateFields(CamelModel):
    """Mutable item fields; ``id`` is intentionally absent."""

    kind_id: str | None = Field(default=None, min_length=1)
    variant_id: str | None = Field(default=None, min_length=1)
    transform: TransformUpdate | None = None
    constraint: ItemConstraint | None = None
    material_overrides: dict[str, str] | None = None
    source: ItemSource | None = None


class RoomUpdateFields(CamelModel):
    """Mutable room fields; ``id`` is intentionally absent."""

    name: str | None = Field(default=None, min_length=1)
    type: RoomType | None = None
    polygon: list[Vec2] | None = Field(default=None, min_length=3)
    wall_labels: dict[str, str] | None = None
    wall_height: float | None = Field(default=None, gt=0)
    wall_thickness: float | None = Field(default=None, gt=0)
    floor_material: str | None = None


class OpeningUpdateFields(CamelModel):
    """Mutable opening fields; ``id`` is intentionally absent."""

    type: OpeningType | None = None
    room_id: str | None = Field(default=None, min_length=1)
    edge_index: int | None = Field(default=None, ge=0)
    offset_from: Literal["start"] | None = None
    offset: float | None = Field(default=None, ge=0)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    sill_height: float | None = Field(default=None, ge=0)
    door: DoorSpec | None = None
    style: str | None = None


class ItemAddOp(CamelModel):
    op: Literal["item.add"]
    item: Item


class ItemRemoveOp(CamelModel):
    op: Literal["item.remove"]
    item_id: str = Field(min_length=1)
    before: Item | None = None


class ItemUpdateOp(CamelModel):
    op: Literal["item.update"]
    item_id: str = Field(min_length=1)
    before: ItemUpdateFields | None = None
    after: ItemUpdateFields


class RoomAddOp(CamelModel):
    op: Literal["room.add"]
    room: Room


class RoomRemoveOp(CamelModel):
    op: Literal["room.remove"]
    room_id: str = Field(min_length=1)
    before: Room | None = None


class RoomUpdateOp(CamelModel):
    op: Literal["room.update"]
    room_id: str = Field(min_length=1)
    before: RoomUpdateFields | None = None
    after: RoomUpdateFields


class OpeningAddOp(CamelModel):
    op: Literal["opening.add"]
    opening: Opening


class OpeningRemoveOp(CamelModel):
    op: Literal["opening.remove"]
    opening_id: str = Field(min_length=1)
    before: Opening | None = None


class OpeningUpdateOp(CamelModel):
    op: Literal["opening.update"]
    opening_id: str = Field(min_length=1)
    before: OpeningUpdateFields | None = None
    after: OpeningUpdateFields


PatchOp: TypeAlias = Annotated[  # noqa: UP040 - Pydantic handles TypeAlias reliably
    ItemAddOp
    | ItemRemoveOp
    | ItemUpdateOp
    | RoomAddOp
    | RoomRemoveOp
    | RoomUpdateOp
    | OpeningAddOp
    | OpeningRemoveOp
    | OpeningUpdateOp,
    Field(discriminator="op"),
]


class ScenePatch(CamelModel):
    """A validated change set between two scene versions."""

    scene_id: str = Field(min_length=1)
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    ops: list[PatchOp]
    actor: Actor = Actor.USER
    turn_id: str | None = None
    proposal_id: str | None = None
    client_mutation_id: str | None = None

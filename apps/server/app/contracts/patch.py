"""Semantic patch operations (PRD §5.4).

Operations are the only way scene state changes. ``before``/``after`` are
partial documents so clients can send minimal updates; the server fills them in
for returned patches and generates inverses for undo.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from app.contracts.base import CamelModel
from app.contracts.enums import Actor
from app.contracts.scene import Item, Opening, Room


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
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_after(self) -> Self:
        if self.after is None:
            raise ValueError("item.update requires 'after'")
        return self


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
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_after(self) -> Self:
        if self.after is None:
            raise ValueError("room.update requires 'after'")
        return self


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
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_after(self) -> Self:
        if self.after is None:
            raise ValueError("opening.update requires 'after'")
        return self


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

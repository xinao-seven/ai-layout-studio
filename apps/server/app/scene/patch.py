"""Apply semantic patch operations and derive their inverses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.contracts.patch import (
    ItemAddOp,
    ItemRemoveOp,
    ItemUpdateOp,
    OpeningAddOp,
    OpeningRemoveOp,
    OpeningUpdateOp,
    PatchOp,
    RoomAddOp,
    RoomRemoveOp,
    RoomUpdateOp,
)
from app.contracts.scene import (
    Anchor,
    Clearance,
    DoorSpec,
    FacingTowardSpec,
    Item,
    ItemConstraint,
    Near,
    Opening,
    Room,
    SceneDocument,
    Transform,
)
from app.geometry.polygon import point_in_polygon
from app.scene.document import validate_document
from app.scene.errors import SceneError

NESTED_MODEL_BY_FIELD: dict[str, type[BaseModel]] = {
    "transform": Transform,
    "constraint": ItemConstraint,
    "anchor": Anchor,
    "near": Near,
    "facing": FacingTowardSpec,
    "clearance": Clearance,
    "door": DoorSpec,
}


def _field_alias_lookup(model_cls: type[BaseModel]) -> dict[str, str]:
    """Map field names and aliases to the JSON alias (camelCase)."""
    lookup: dict[str, str] = {}
    for name, field in model_cls.model_fields.items():
        alias = field.alias or name
        lookup[name] = alias
        lookup[alias] = alias
    return lookup


def _canonicalise(model_cls: type[BaseModel], updates: dict[str, Any]) -> dict[str, Any]:
    lookup = _field_alias_lookup(model_cls)
    canonical: dict[str, Any] = {}
    for key, value in updates.items():
        alias = lookup.get(key, key)
        field_name = next(
            (
                name
                for name, field in model_cls.model_fields.items()
                if (field.alias or name) == alias
            ),
            alias,
        )
        if isinstance(value, dict):
            nested_cls = NESTED_MODEL_BY_FIELD.get(field_name)
            if nested_cls is not None:
                canonical[alias] = _canonicalise(nested_cls, value)
                continue
        canonical[alias] = value
    return canonical


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _merge_model[ModelT: BaseModel](model: ModelT, updates: dict[str, Any]) -> ModelT:
    model_cls = type(model)
    current = model.model_dump(mode="python", by_alias=True)
    merged = _deep_merge(current, _canonicalise(model_cls, updates))
    try:
        return model_cls.model_validate(merged)
    except ValidationError as error:
        raise SceneError(
            "invalid_input",
            f"Invalid {model_cls.__name__} update.",
            "Check field names, types and units.",
            {"errors": error.errors(include_url=False, include_context=False)},
        ) from error


def _find_item(document: SceneDocument, item_id: str) -> Item:
    for item in document.items:
        if item.id == item_id:
            return item
    raise SceneError(
        "not_found",
        f"Item {item_id!r} does not exist.",
        "Query the scene for the latest item ids.",
        {"itemId": item_id},
    )


def _find_room(document: SceneDocument, room_id: str) -> Room:
    for room in document.rooms:
        if room.id == room_id:
            return room
    raise SceneError(
        "unknown_room",
        f"Room {room_id!r} does not exist.",
        "Query the scene for the latest room ids.",
        {"roomId": room_id},
    )


def _find_opening(document: SceneDocument, opening_id: str) -> Opening:
    for opening in document.openings:
        if opening.id == opening_id:
            return opening
    raise SceneError(
        "not_found",
        f"Opening {opening_id!r} does not exist.",
        "Query the scene for the latest opening ids.",
        {"openingId": opening_id},
    )


def _item_dict(item: Item) -> dict[str, Any]:
    return item.model_dump(mode="python", by_alias=True)


def _room_dict(room: Room) -> dict[str, Any]:
    return room.model_dump(mode="python", by_alias=True)


def _opening_dict(opening: Opening) -> dict[str, Any]:
    return opening.model_dump(mode="python", by_alias=True)


def _remove_room(document: SceneDocument, room: Room) -> None:
    if any(opening.room_id == room.id for opening in document.openings):
        raise SceneError(
            "invalid_input",
            f"Room {room.id} still has openings.",
            "Remove the room's doors and windows first.",
            {"roomId": room.id},
        )
    occupied = [
        item.id
        for item in document.items
        if (item.constraint is not None and item.constraint.room_id == room.id)
        or point_in_polygon((item.transform.position[0], item.transform.position[2]), room.polygon)
    ]
    if occupied:
        raise SceneError(
            "invalid_input",
            f"Room {room.id} still contains furniture.",
            "Remove the furniture first or move it to another room.",
            {"roomId": room.id, "itemIds": occupied},
        )
    document.rooms = [candidate for candidate in document.rooms if candidate.id != room.id]


def apply_ops(
    document: SceneDocument,
    ops: list[PatchOp],
    *,
    validate: bool = True,
) -> tuple[SceneDocument, list[PatchOp]]:
    """Apply ``ops`` to a copy of ``document``.

    Returns the new document and *resolved* ops (``before``/``after`` filled
    with full snapshots) so the same patch can be inverted and replayed.
    """
    working = document.model_copy(deep=True)
    resolved: list[PatchOp] = []

    for op in ops:
        if isinstance(op, ItemAddOp):
            if any(item.id == op.item.id for item in working.items):
                raise SceneError(
                    "invalid_input",
                    f"Item {op.item.id!r} already exists.",
                    "Use update_item or mint a new item id.",
                )
            item = op.item.model_copy(deep=True)
            working.items.append(item)
            resolved.append(ItemAddOp(op="item.add", item=item))
        elif isinstance(op, ItemRemoveOp):
            item = _find_item(working, op.item_id)
            working.items = [candidate for candidate in working.items if candidate.id != item.id]
            resolved.append(ItemRemoveOp(op="item.remove", item_id=item.id, before=item))
        elif isinstance(op, ItemUpdateOp):
            current_item = _find_item(working, op.item_id)
            updated_item = _merge_model(current_item, op.after or {})
            item_index = next(
                index for index, candidate in enumerate(working.items) if candidate.id == op.item_id
            )
            working.items[item_index] = updated_item
            resolved.append(
                ItemUpdateOp(
                    op="item.update",
                    item_id=op.item_id,
                    before=_item_dict(current_item),
                    after=_item_dict(updated_item),
                )
            )
        elif isinstance(op, RoomAddOp):
            if any(room.id == op.room.id for room in working.rooms):
                raise SceneError(
                    "invalid_input",
                    f"Room {op.room.id!r} already exists.",
                    "Mint a new room id.",
                )
            room = op.room.model_copy(deep=True)
            working.rooms.append(room)
            resolved.append(RoomAddOp(op="room.add", room=room))
        elif isinstance(op, RoomRemoveOp):
            room = _find_room(working, op.room_id)
            _remove_room(working, room)
            resolved.append(RoomRemoveOp(op="room.remove", room_id=room.id, before=room))
        elif isinstance(op, RoomUpdateOp):
            current_room = _find_room(working, op.room_id)
            updated_room = _merge_model(current_room, op.after or {})
            room_index = next(
                index for index, candidate in enumerate(working.rooms) if candidate.id == op.room_id
            )
            working.rooms[room_index] = updated_room
            resolved.append(
                RoomUpdateOp(
                    op="room.update",
                    room_id=op.room_id,
                    before=_room_dict(current_room),
                    after=_room_dict(updated_room),
                )
            )
        elif isinstance(op, OpeningAddOp):
            if any(candidate.id == op.opening.id for candidate in working.openings):
                raise SceneError(
                    "invalid_input",
                    f"Opening {op.opening.id!r} already exists.",
                    "Mint a new opening id.",
                )
            opening = op.opening.model_copy(deep=True)
            working.openings.append(opening)
            resolved.append(OpeningAddOp(op="opening.add", opening=opening))
        elif isinstance(op, OpeningRemoveOp):
            opening = _find_opening(working, op.opening_id)
            working.openings = [
                candidate for candidate in working.openings if candidate.id != opening.id
            ]
            resolved.append(
                OpeningRemoveOp(op="opening.remove", opening_id=opening.id, before=opening)
            )
        elif isinstance(op, OpeningUpdateOp):
            current_opening = _find_opening(working, op.opening_id)
            updated_opening = _merge_model(current_opening, op.after or {})
            opening_index = next(
                index
                for index, candidate in enumerate(working.openings)
                if candidate.id == op.opening_id
            )
            working.openings[opening_index] = updated_opening
            resolved.append(
                OpeningUpdateOp(
                    op="opening.update",
                    opening_id=op.opening_id,
                    before=_opening_dict(current_opening),
                    after=_opening_dict(updated_opening),
                )
            )

    if validate:
        validate_document(working)
    return working, resolved


def invert_resolved_ops(ops: list[PatchOp]) -> list[PatchOp]:
    """Return inverse ops in reverse application order."""
    inverse: list[PatchOp] = []
    for op in reversed(ops):
        if isinstance(op, ItemAddOp):
            inverse.append(ItemRemoveOp(op="item.remove", item_id=op.item.id, before=op.item))
        elif isinstance(op, ItemRemoveOp):
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert removal of {op.item_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
            inverse.append(ItemAddOp(op="item.add", item=op.before))
        elif isinstance(op, ItemUpdateOp):
            inverse.append(
                ItemUpdateOp(
                    op="item.update",
                    item_id=op.item_id,
                    before=op.after,
                    after=op.before,
                )
            )
        elif isinstance(op, RoomAddOp):
            inverse.append(RoomRemoveOp(op="room.remove", room_id=op.room.id, before=op.room))
        elif isinstance(op, RoomRemoveOp):
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert removal of {op.room_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
            inverse.append(RoomAddOp(op="room.add", room=op.before))
        elif isinstance(op, RoomUpdateOp):
            inverse.append(
                RoomUpdateOp(
                    op="room.update",
                    room_id=op.room_id,
                    before=op.after,
                    after=op.before,
                )
            )
        elif isinstance(op, OpeningAddOp):
            inverse.append(
                OpeningRemoveOp(op="opening.remove", opening_id=op.opening.id, before=op.opening)
            )
        elif isinstance(op, OpeningRemoveOp):
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert removal of {op.opening_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
            inverse.append(OpeningAddOp(op="opening.add", opening=op.before))
        elif isinstance(op, OpeningUpdateOp):
            inverse.append(
                OpeningUpdateOp(
                    op="opening.update",
                    opening_id=op.opening_id,
                    before=op.after,
                    after=op.before,
                )
            )
    return inverse

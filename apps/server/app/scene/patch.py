"""Apply semantic patch operations and derive their inverses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.contracts.enums import FacingToward
from app.contracts.patch import (
    ItemAddOp,
    ItemRemoveOp,
    ItemUpdateFields,
    ItemUpdateOp,
    OpeningAddOp,
    OpeningRemoveOp,
    OpeningUpdateFields,
    OpeningUpdateOp,
    PatchOp,
    RoomAddOp,
    RoomRemoveOp,
    RoomUpdateFields,
    RoomUpdateOp,
    SceneUpdateFields,
    SceneUpdateOp,
    TransformUpdate,
)
from app.contracts.rules import Rules, get_rules
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
from app.geometry.collision import validate_placement, validate_room_change
from app.geometry.errors import GeometryError
from app.geometry.openings import GeometryWarning, reanchor_openings
from app.geometry.polygon import point_in_polygon
from app.scene.document import CatalogMap, validate_document
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


def _item_update_fields(item: Item) -> ItemUpdateFields:
    return ItemUpdateFields(
        kind_id=item.kind_id,
        variant_id=item.variant_id,
        transform=TransformUpdate(
            position=item.transform.position,
            rotation_y=item.transform.rotation_y,
            scale=item.transform.scale,
        ),
        constraint=item.constraint,
        material_overrides=item.material_overrides,
        source=item.source,
    )


def _room_update_fields(room: Room) -> RoomUpdateFields:
    return RoomUpdateFields(
        name=room.name,
        type=room.type,
        polygon=room.polygon,
        wall_labels=room.wall_labels,
        wall_height=room.wall_height,
        wall_thickness=room.wall_thickness,
        floor_material=room.floor_material,
    )


def _opening_update_fields(opening: Opening) -> OpeningUpdateFields:
    return OpeningUpdateFields(
        type=opening.type,
        room_id=opening.room_id,
        edge_index=opening.edge_index,
        offset_from=opening.offset_from,
        offset=opening.offset,
        width=opening.width,
        height=opening.height,
        sill_height=opening.sill_height,
        door=opening.door,
        style=opening.style,
    )


def _scene_update_fields(document: SceneDocument) -> SceneUpdateFields:
    return SceneUpdateFields(
        north=document.north,
        environment=document.environment,
    )


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


def _clear_opening_reference(item: Item, opening_id: str) -> Item | None:
    """Drop references to a removed opening so constraints cannot dangle."""
    constraint = item.constraint
    if constraint is None:
        return None
    updates: dict[str, Any] = {}
    if constraint.anchor is not None and constraint.anchor.opening_id == opening_id:
        updates["anchor"] = constraint.anchor.model_copy(update={"opening_id": None})
    if constraint.near is not None and constraint.near.opening_id == opening_id:
        updates["near"] = constraint.near.model_copy(update={"opening_id": None})
    if (
        constraint.facing is not None
        and not isinstance(constraint.facing, str)
        and constraint.facing.id == opening_id
    ):
        updates["facing"] = constraint.facing.model_copy(update={"id": None})
    if not updates:
        return None
    return item.model_copy(update={"constraint": constraint.model_copy(update=updates)})


def _clear_removed_item_reference(item: Item, removed_item_id: str) -> Item | None:
    """Drop near/facing references to an item that is being removed."""
    constraint = item.constraint
    if constraint is None:
        return None
    updates: dict[str, Any] = {}
    near = constraint.near
    if near is not None and near.item_id == removed_item_id:
        if near.opening_id is None:
            updates["near"] = None
        else:
            updates["near"] = near.model_copy(update={"item_id": None})
    facing = constraint.facing
    if (
        facing is not None
        and not isinstance(facing, str)
        and facing.toward == FacingToward.ITEM
        and facing.id == removed_item_id
    ):
        updates["facing"] = None
    if not updates:
        return None
    return item.model_copy(update={"constraint": constraint.model_copy(update=updates)})


def _clear_removed_item_references(
    working: SceneDocument,
    removed_item_id: str,
    resolved: list[PatchOp],
) -> None:
    """Clear constraints that point at an item being removed."""
    for item_index, item in enumerate(working.items):
        if item.id == removed_item_id:
            continue
        cleared = _clear_removed_item_reference(item, removed_item_id)
        if cleared is None:
            continue
        working.items[item_index] = cleared
        resolved.append(
            ItemUpdateOp(
                op="item.update",
                item_id=item.id,
                before=_item_update_fields(item),
                after=_item_update_fields(cleared),
            )
        )


def _clear_item_references(
    working: SceneDocument,
    opening_id: str,
    resolved: list[PatchOp],
) -> None:
    """Clear item constraints that point at an opening being removed."""
    for item_index, item in enumerate(working.items):
        cleared = _clear_opening_reference(item, opening_id)
        if cleared is None:
            continue
        working.items[item_index] = cleared
        resolved.append(
            ItemUpdateOp(
                op="item.update",
                item_id=item.id,
                before=_item_update_fields(item),
                after=_item_update_fields(cleared),
            )
        )


def apply_ops(
    document: SceneDocument,
    ops: list[PatchOp],
    *,
    validate: bool = True,
    reanchor: bool = True,
    check_placement: bool = True,
    rules: Rules | None = None,
    catalog: CatalogMap | None = None,
    warnings: list[GeometryWarning] | None = None,
) -> tuple[SceneDocument, list[PatchOp]]:
    """Apply ``ops`` to a copy of ``document``.

    Returns the new document and *resolved* ops (``before``/``after`` filled
    with full snapshots) so the same patch can be inverted and replayed.
    """
    working = document.model_copy(deep=True)
    resolved: list[PatchOp] = []
    changed_item_ids: set[str] = set()
    changed_opening_ids: set[str] = set()
    room_changes: list[tuple[str, list[tuple[float, float]], list[tuple[float, float]]]] = []

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
            changed_item_ids.add(item.id)
            resolved.append(ItemAddOp(op="item.add", item=item))
        elif isinstance(op, ItemRemoveOp):
            item = _find_item(working, op.item_id)
            _clear_removed_item_references(working, item.id, resolved)
            working.items = [candidate for candidate in working.items if candidate.id != item.id]
            resolved.append(ItemRemoveOp(op="item.remove", item_id=item.id, before=item))
        elif isinstance(op, ItemUpdateOp):
            current_item = _find_item(working, op.item_id)
            updated_item = _merge_model(
                current_item,
                op.after.model_dump(mode="python", by_alias=True, exclude_unset=True),
            )
            item_index = next(
                index for index, candidate in enumerate(working.items) if candidate.id == op.item_id
            )
            if (
                updated_item.transform.position != current_item.transform.position
                and updated_item.constraint is not None
            ):
                position = (
                    updated_item.transform.position[0],
                    updated_item.transform.position[2],
                )
                containing = next(
                    (room for room in working.rooms if point_in_polygon(position, room.polygon)),
                    None,
                )
                if containing is not None:
                    updated_item = updated_item.model_copy(
                        update={
                            "constraint": updated_item.constraint.model_copy(
                                update={"room_id": containing.id}
                            )
                        }
                    )
            working.items[item_index] = updated_item
            changed_item_ids.add(op.item_id)
            resolved.append(
                ItemUpdateOp(
                    op="item.update",
                    item_id=op.item_id,
                    before=_item_update_fields(current_item),
                    after=_item_update_fields(updated_item),
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
            updated_room = _merge_model(
                current_room,
                op.after.model_dump(mode="python", by_alias=True, exclude_unset=True),
            )
            room_index = next(
                index for index, candidate in enumerate(working.rooms) if candidate.id == op.room_id
            )
            working.rooms[room_index] = updated_room
            resolved.append(
                RoomUpdateOp(
                    op="room.update",
                    room_id=op.room_id,
                    before=_room_update_fields(current_room),
                    after=_room_update_fields(updated_room),
                )
            )
            if updated_room.polygon != current_room.polygon:
                room_changes.append(
                    (op.room_id, list(current_room.polygon), list(updated_room.polygon))
                )
            if reanchor and updated_room.polygon != current_room.polygon:
                room_openings = [
                    opening for opening in working.openings if opening.room_id == op.room_id
                ]
                kept, room_warnings = reanchor_openings(current_room, updated_room, room_openings)
                if warnings is not None:
                    warnings.extend(room_warnings)
                for warning in room_warnings:
                    if warning.code == "opening_reanchored":
                        opening_id = warning.details.get("openingId")
                        if isinstance(opening_id, str):
                            changed_opening_ids.add(opening_id)
                kept_by_id = {opening.id: opening for opening in kept}
                for previous in room_openings:
                    replacement = kept_by_id.get(previous.id)
                    if replacement is None:
                        # Remove dangling item references before dropping the opening.
                        _clear_item_references(working, previous.id, resolved)
                        working.openings = [
                            candidate
                            for candidate in working.openings
                            if candidate.id != previous.id
                        ]
                        resolved.append(
                            OpeningRemoveOp(
                                op="opening.remove",
                                opening_id=previous.id,
                                before=previous,
                            )
                        )
                    elif replacement != previous:
                        changed_opening_ids.add(previous.id)
                        opening_index = next(
                            index
                            for index, candidate in enumerate(working.openings)
                            if candidate.id == previous.id
                        )
                        working.openings[opening_index] = replacement
                        resolved.append(
                            OpeningUpdateOp(
                                op="opening.update",
                                opening_id=previous.id,
                                before=_opening_update_fields(previous),
                                after=_opening_update_fields(replacement),
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
            changed_opening_ids.add(opening.id)
            resolved.append(OpeningAddOp(op="opening.add", opening=opening))
        elif isinstance(op, OpeningRemoveOp):
            opening = _find_opening(working, op.opening_id)
            _clear_item_references(working, opening.id, resolved)
            working.openings = [
                candidate for candidate in working.openings if candidate.id != opening.id
            ]
            resolved.append(
                OpeningRemoveOp(op="opening.remove", opening_id=opening.id, before=opening)
            )
        elif isinstance(op, OpeningUpdateOp):
            current_opening = _find_opening(working, op.opening_id)
            updated_opening = _merge_model(
                current_opening,
                op.after.model_dump(mode="python", by_alias=True, exclude_unset=True),
            )
            opening_index = next(
                index
                for index, candidate in enumerate(working.openings)
                if candidate.id == op.opening_id
            )
            working.openings[opening_index] = updated_opening
            changed_opening_ids.add(op.opening_id)
            resolved.append(
                OpeningUpdateOp(
                    op="opening.update",
                    opening_id=op.opening_id,
                    before=_opening_update_fields(current_opening),
                    after=_opening_update_fields(updated_opening),
                )
            )
        elif isinstance(op, SceneUpdateOp):
            # Snapshot the *current* working state (not the patch's base) so
            # consecutive scene.update ops chain before/after correctly.
            before_fields = _scene_update_fields(working)
            updated_document = _merge_model(
                working,
                op.after.model_dump(mode="python", by_alias=True, exclude_unset=True),
            )
            working = updated_document
            resolved.append(
                SceneUpdateOp(
                    op="scene.update",
                    before=before_fields,
                    after=_scene_update_fields(updated_document),
                )
            )

    if validate:
        validate_document(working, rules=rules, catalog=catalog)
        if catalog is not None and check_placement:
            try:
                validate_placement(
                    working,
                    item_ids=changed_item_ids,
                    opening_ids=changed_opening_ids,
                    catalog=catalog,
                    rules=rules or get_rules(),
                )
                for room_id, old_polygon, new_polygon in room_changes:
                    validate_room_change(
                        working,
                        room_id=room_id,
                        old_polygon=old_polygon,
                        new_polygon=new_polygon,
                        catalog=catalog,
                        rules=rules or get_rules(),
                    )
            except GeometryError as error:
                raise SceneError(error.code, error.message, error.suggest, error.details) from error
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
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert update of {op.item_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
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
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert update of {op.room_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
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
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    f"Cannot invert update of {op.opening_id}: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
            inverse.append(
                OpeningUpdateOp(
                    op="opening.update",
                    opening_id=op.opening_id,
                    before=op.after,
                    after=op.before,
                )
            )
        elif isinstance(op, SceneUpdateOp):
            if op.before is None:
                raise SceneError(
                    "invalid_input",
                    "Cannot invert scene.update: before snapshot missing.",
                    "Regenerate the patch on the server.",
                )
            inverse.append(
                SceneUpdateOp(
                    op="scene.update",
                    before=op.after,
                    after=op.before,
                )
            )
    return inverse

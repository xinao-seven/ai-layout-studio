"""Scene document validation and derived queries."""

from __future__ import annotations

from collections.abc import Mapping

from app.contracts.catalog import CatalogEntry
from app.contracts.rules import Rules, get_rules
from app.contracts.scene import Item, SceneDocument, Vec2
from app.geometry.errors import GeometryError
from app.geometry.openings import validate_openings
from app.geometry.polygon import is_ccw, point_in_polygon, polygon_area, validate_polygon
from app.geometry.walls import derive_wall_segments
from app.scene.errors import SceneError

CatalogMap = Mapping[str, CatalogEntry]


def _raise_scene_error(error: GeometryError) -> None:
    raise SceneError(error.code, error.message, error.suggest, error.details) from error


def document_area(document: SceneDocument) -> float:
    """Sum of room polygon areas in square meters."""
    return sum(polygon_area(room.polygon) for room in document.rooms)


def _item_position(item: Item) -> Vec2:
    return (item.transform.position[0], item.transform.position[2])


def _validate_scale(
    item: Item,
    document: SceneDocument,
    catalog: CatalogMap | None,
    rules: Rules,
) -> None:
    """Enforce the uniform-scale envelope and the ceiling height.

    Scale is a user-facing editor control, so it needs the same server-side
    refusal path as placement: the client clamp is a convenience, not the
    authority. The ceiling check only applies to resized items so legacy
    fixtures are never re-litigated.
    """
    scale = item.transform.scale
    if scale < rules.item_scale_min - 1e-9 or scale > rules.item_scale_max + 1e-9:
        raise SceneError(
            "unrealistic",
            (
                f"Item {item.id} scale {scale:.2f} is outside "
                f"{rules.item_scale_min:.2f}–{rules.item_scale_max:.2f}."
            ),
            "Resize the item within the allowed range.",
            {
                "itemId": item.id,
                "ruleId": "item_scale_bounds",
                "scale": scale,
                "min": rules.item_scale_min,
                "max": rules.item_scale_max,
            },
        )
    if catalog is None or abs(scale - 1.0) <= 1e-9:
        return
    entry = catalog.get(item.kind_id)
    if entry is None or not entry.variants:
        return
    variant = next(
        (candidate for candidate in entry.variants if candidate.id == item.variant_id),
        entry.variants[0],
    )
    position = _item_position(item)
    room = next(
        (
            candidate
            for candidate in document.rooms
            if point_in_polygon(position, candidate.polygon)
        ),
        None,
    )
    ceiling = room.wall_height if room and room.wall_height else rules.wall_height
    height = variant.size_m[2] * scale
    if height > ceiling + 1e-6:
        raise SceneError(
            "unrealistic",
            (f"Item {item.id} would be {height:.2f} m tall, above the {ceiling:.2f} m ceiling."),
            "Reduce the size or use a shorter variant.",
            {
                "itemId": item.id,
                "ruleId": "item_exceeds_ceiling",
                "height": round(height, 3),
                "ceiling": round(ceiling, 3),
            },
        )


def _validate_items(
    document: SceneDocument,
    catalog: CatalogMap | None,
    rules: Rules | None = None,
) -> None:
    effective_rules = rules or get_rules()
    rooms = {room.id: room for room in document.rooms}
    item_ids = {item.id for item in document.items}
    if len(item_ids) != len(document.items):
        raise SceneError(
            "invalid_input",
            "Item ids must be unique within a scene.",
            "Mint a new item id.",
        )

    opening_ids = {opening.id for opening in document.openings}
    for item in document.items:
        _validate_scale(item, document, catalog, effective_rules)
        position = _item_position(item)
        if document.rooms and not any(
            point_in_polygon(position, room.polygon) for room in document.rooms
        ):
            raise SceneError(
                "out_of_room",
                f"Item {item.id} is not inside any room.",
                "Move the item into a room or add the room first.",
                {"itemId": item.id, "position": list(position)},
            )

        constraint = item.constraint
        if constraint is None:
            continue
        if constraint.room_id is not None and constraint.room_id not in rooms:
            raise SceneError(
                "unknown_room",
                f"Item {item.id} constraint references room {constraint.room_id}.",
                "Query the scene for current room ids.",
            )
        anchor = constraint.anchor
        if anchor is not None:
            if anchor.wall_id is not None:
                room_id, _, edge_part = anchor.wall_id.partition(":e")
                if room_id not in rooms or not edge_part.isdigit():
                    raise SceneError(
                        "not_found",
                        f"Item {item.id} references unknown wall {anchor.wall_id}.",
                        "Use a wall id from query_scene.",
                    )
                edge_index = int(edge_part)
                if edge_index >= len(rooms[room_id].polygon):
                    raise SceneError(
                        "not_found",
                        f"Item {item.id} wall edge is out of range.",
                        "Use a wall id from query_scene.",
                    )
            if anchor.wall_label is not None:
                room = rooms.get(constraint.room_id) if constraint.room_id else None
                labels = (room.wall_labels or {}) if room else {}
                if anchor.wall_label not in labels.values():
                    raise SceneError(
                        "not_found",
                        f"Item {item.id} references unknown wall label {anchor.wall_label!r}.",
                        "Use a label from query_scene.",
                    )
            if anchor.opening_id is not None and anchor.opening_id not in opening_ids:
                raise SceneError(
                    "not_found",
                    f"Item {item.id} references unknown opening {anchor.opening_id}.",
                    "Use an opening id from query_scene.",
                )
        near = constraint.near
        if near is not None:
            if near.item_id is not None and near.item_id not in item_ids:
                raise SceneError(
                    "not_found",
                    f"Item {item.id} is near unknown item {near.item_id}.",
                    "Use an item id from query_scene.",
                )
            if near.opening_id is not None and near.opening_id not in opening_ids:
                raise SceneError(
                    "not_found",
                    f"Item {item.id} is near unknown opening {near.opening_id}.",
                    "Use an opening id from query_scene.",
                )

        if catalog is not None:
            entry = catalog.get(item.kind_id)
            if entry is None:
                raise SceneError(
                    "unknown_kind",
                    f"Unknown catalog kind {item.kind_id!r}.",
                    "Search the catalog for a valid kind.",
                )
            if all(variant.id != item.variant_id for variant in entry.variants):
                raise SceneError(
                    "variant_missing",
                    f"Kind {item.kind_id!r} has no variant {item.variant_id!r}.",
                    "Use the default variant or search the catalog.",
                )


def validate_document(
    document: SceneDocument,
    *,
    rules: Rules | None = None,
    catalog: CatalogMap | None = None,
) -> None:
    """Validate a complete scene document (L1 + basic L2 checks)."""
    rules = rules or get_rules()
    room_ids: set[str] = set()
    for room in document.rooms:
        if room.id in room_ids:
            raise SceneError(
                "invalid_input",
                f"Duplicate room id {room.id!r}.",
                "Mint a new room id.",
            )
        room_ids.add(room.id)
        try:
            validate_polygon(room.polygon, min_area=rules.min_room_area)
            if not is_ccw(room.polygon):
                raise GeometryError(
                    "invalid_polygon",
                    f"Room {room.id} polygon must be counter-clockwise.",
                    "Reverse the vertex order.",
                )
            derive_wall_segments(room, document.defaults)
        except GeometryError as error:
            _raise_scene_error(error)

    try:
        validate_openings(document, rules)
    except GeometryError as error:
        _raise_scene_error(error)

    _validate_items(document, catalog, rules)

    for timestamp_name in ("created_at", "updated_at"):
        value = getattr(document, timestamp_name)
        if value.tzinfo is None:
            raise SceneError(
                "invalid_input",
                f"Scene {timestamp_name} must be timezone-aware UTC.",
                "Use an ISO 8601 timestamp with a timezone.",
            )

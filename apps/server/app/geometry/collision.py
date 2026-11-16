"""L2 spatial validation for manual placements (PRD §6.3).

The client performs a fast OBB/boundary precheck for feedback. The server
repeats the check against the authoritative scene and also validates door
clearance, so a stale or malicious client cannot commit overlapping furniture.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from app.contracts.catalog import CatalogEntry
from app.contracts.rules import Rules
from app.contracts.scene import Item, Opening, Room, SceneDocument, Vec2
from app.geometry.errors import GeometryError
from app.geometry.obb import OBB, obb_from_transform, obb_overlap
from app.geometry.openings import opening_center
from app.geometry.polygon import inset_polygon, point_in_polygon
from app.geometry.walls import wall_inner_normal

CATALOG_FALLBACK_SIZE: Vec2 = (0.8, 0.8)
CATALOG_FALLBACK_HEIGHT = 0.8
COLLISION_TOLERANCE = -0.005


def item_size(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> Vec2:
    """Return (width, depth) in meters; unknown kinds use a conservative box."""
    if catalog is None:
        return CATALOG_FALLBACK_SIZE
    entry = catalog.get(item.kind_id)
    if entry is None:
        return CATALOG_FALLBACK_SIZE
    variant = next(
        (candidate for candidate in entry.variants if candidate.id == item.variant_id),
        entry.variants[0],
    )
    return (variant.size_m[0], variant.size_m[1])


def item_allows_overlap(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> bool:
    if catalog is None:
        return False
    entry = catalog.get(item.kind_id)
    return bool(entry and entry.rules and entry.rules.allow_overlap)


def item_height(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> float:
    if catalog is None:
        return CATALOG_FALLBACK_HEIGHT
    entry = catalog.get(item.kind_id)
    if entry is None:
        return CATALOG_FALLBACK_HEIGHT
    variant = next(
        (candidate for candidate in entry.variants if candidate.id == item.variant_id),
        entry.variants[0],
    )
    return variant.size_m[2]


def _is_circulation_blocker(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> bool:
    """Large floor furniture whose gaps must stay walkable."""
    if item_allows_overlap(item, catalog) or catalog is None:
        return False
    entry = catalog.get(item.kind_id)
    if entry is None:
        return False
    if entry.category.value in {"sofa", "bed", "storage"}:
        return True
    return item.kind_id.startswith(("wardrobe", "tv_stand", "kitchen_", "fridge", "bed_"))


def is_circulation_blocker(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> bool:
    """Public predicate shared by validation, verification and the solver."""
    return _is_circulation_blocker(item, catalog)


def catalog_items_grouped(
    first: Item,
    second: Item,
    catalog: Mapping[str, CatalogEntry] | None,
) -> bool:
    """True when the catalog declares the two kinds as a functional group.

    Grouped pieces (bed + nightstand, sofa + coffee table) are allowed to sit
    closer than the secondary passage width; unrelated large pieces are not.
    """
    if catalog is None:
        return False
    first_entry = catalog.get(first.kind_id)
    second_entry = catalog.get(second.kind_id)
    if first_entry is None or second_entry is None:
        return False
    first_group = set(first_entry.rules.group_with) if first_entry.rules else set()
    second_group = set(second_entry.rules.group_with) if second_entry.rules else set()
    return second_entry.kind_id in first_group or first_entry.kind_id in second_group


def item_obb(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> OBB:
    return obb_from_transform(
        item.transform.position,
        item_size(item, catalog),
        item.transform.rotation_y,
    )


def _room_for_point(document: SceneDocument, point: Vec2) -> Room | None:
    for room in document.rooms:
        if point_in_polygon(point, room.polygon):
            return room
    return None


def item_room_id(document: SceneDocument, item: Item) -> str | None:
    """Resolve the containing room from the item's position.

    The stored constraint can be stale after a generic ``item.update`` commit,
    so it is only a fallback. This prevents a stale roomId from exempting an
    item from cross-item collision checks.
    """
    room = _room_for_point(document, (item.transform.position[0], item.transform.position[2]))
    if room is not None:
        return room.id
    if item.constraint is not None:
        return item.constraint.room_id
    return None


def validate_item_collisions(
    document: SceneDocument,
    catalog: Mapping[str, CatalogEntry] | None,
) -> None:
    """Reject OBB overlaps between floor items.

    Items whose catalog entry (or either side of the pair) sets
    ``rules.allowOverlap`` are ignored: rugs, table lamps and small decor are
    allowed to sit under or on other furniture. Items in different rooms are
    also ignored because walls separate their footprints.
    """
    boxes = [(item, item_obb(item, catalog)) for item in document.items]
    for index, (first, first_box) in enumerate(boxes):
        if item_allows_overlap(first, catalog):
            continue
        first_room = item_room_id(document, first)
        for second, second_box in boxes[index + 1 :]:
            if item_allows_overlap(second, catalog):
                continue
            second_room = item_room_id(document, second)
            if first_room is not None and second_room is not None and first_room != second_room:
                continue
            if obb_overlap(first_box, second_box, tolerance=COLLISION_TOLERANCE):
                raise GeometryError(
                    "collision",
                    f"Items {first.id} and {second.id} overlap.",
                    "Move one item, rotate it, or choose a smaller variant.",
                    {
                        "itemIds": [first.id, second.id],
                        "kindIds": [first.kind_id, second.kind_id],
                    },
                )


def _window_clearance_obb(room: Room, opening: Opening, rules: Rules) -> OBB | None:
    polygon = room.polygon
    if opening.edge_index >= len(polygon):
        return None
    a = polygon[opening.edge_index]
    b = polygon[(opening.edge_index + 1) % len(polygon)]
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return None
    tangent = (dx / length, dz / length)
    normal = wall_inner_normal(a, b)
    centre = opening_center(room, opening)
    thickness = room.wall_thickness if room.wall_thickness is not None else rules.wall_thickness
    # Start at the interior wall face, not the centerline; ROOM_INSET otherwise
    # makes a 5 cm strip unreachable by any valid furniture.
    offset = thickness / 2.0 + rules.window_clear_depth / 2.0
    inside = (
        centre[0] + normal[0] * offset,
        centre[1] + normal[1] * offset,
    )
    return OBB(
        center=inside,
        size=(opening.width, rules.window_clear_depth),
        yaw=math.atan2(-tangent[1], tangent[0]),
    )


def validate_window_clearance(
    document: SceneDocument,
    *,
    item_ids: set[str],
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> None:
    """Reject tall furniture that blocks a window's interior clearance strip."""
    rooms = {room.id: room for room in document.rooms}
    changed = [item for item in document.items if item.id in item_ids]
    for opening in document.openings:
        if opening.type != "window":
            continue
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        clearance = _window_clearance_obb(room, opening, rules)
        if clearance is None:
            continue
        sill = opening.sill_height if opening.sill_height is not None else 0.9
        for item in changed:
            if item_allows_overlap(item, catalog):
                continue
            if item_height(item, catalog) <= sill + 1e-6:
                continue
            if obb_overlap(item_obb(item, catalog), clearance, tolerance=COLLISION_TOLERANCE):
                raise GeometryError(
                    "blocks_window",
                    f"Item {item.id} blocks window {opening.id}.",
                    "Use a lower item below the sill or move it away from the window.",
                    {"itemId": item.id, "openingId": opening.id, "sillHeight": sill},
                )


def validate_walkways(
    document: SceneDocument,
    *,
    item_ids: set[str],
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> None:
    """Reject narrow gaps between large furniture pieces (secondary passages)."""
    changed = [
        item
        for item in document.items
        if item.id in item_ids and _is_circulation_blocker(item, catalog)
    ]
    if not changed:
        return
    others = [item for item in document.items if item.id not in item_ids]
    for item in changed:
        box = item_obb(item, catalog).to_polygon()
        for other in others:
            if not _is_circulation_blocker(other, catalog):
                continue
            other_room = item_room_id(document, other)
            changed_room = item_room_id(document, item)
            if changed_room is not None and other_room is not None and changed_room != other_room:
                continue
            if catalog_items_grouped(item, other, catalog):
                continue
            gap = float(box.distance(item_obb(other, catalog).to_polygon()))
            if 1e-6 < gap < rules.passage_secondary - 1e-6:
                raise GeometryError(
                    "walkway_too_narrow",
                    f"Only {gap:.2f} m between {item.id} and {other.id}.",
                    "Leave at least 0.6 m between large furniture pieces.",
                    {
                        "itemIds": [item.id, other.id],
                        "gap": round(gap, 3),
                        "required": rules.passage_secondary,
                    },
                )


def _opening_clearance_obb(room: Room, opening: Opening, rules: Rules) -> OBB | None:
    polygon = room.polygon
    if opening.edge_index >= len(polygon):
        return None
    a = polygon[opening.edge_index]
    b = polygon[(opening.edge_index + 1) % len(polygon)]
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return None
    tangent = (dx / length, dz / length)
    width = max(opening.width, rules.door_approach_width)
    # Door clearance extends on both sides of the wall centerline.
    return OBB(
        center=opening_center(room, opening),
        size=(width, rules.door_clear_depth * 2.0),
        yaw=math.atan2(-tangent[1], tangent[0]),
    )


def validate_door_clearance(
    document: SceneDocument,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> None:
    """Reject furniture standing in a door's approach clearance."""
    rooms = {room.id: room for room in document.rooms}
    for opening in document.openings:
        if opening.type != "door":
            continue
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        clearance = _opening_clearance_obb(room, opening, rules)
        if clearance is None:
            continue
        for item in document.items:
            if item_allows_overlap(item, catalog):
                continue
            box = item_obb(item, catalog)
            if obb_overlap(box, clearance, tolerance=COLLISION_TOLERANCE):
                raise GeometryError(
                    "blocks_door",
                    f"Item {item.id} blocks the door {opening.id}.",
                    "Keep the door approach clear (at least 0.6 m deep).",
                    {"itemId": item.id, "openingId": opening.id},
                )


def window_clearance_obb(room: Room, opening: Opening, rules: Rules) -> OBB | None:
    """Public wrapper used by the verifier and VLM geometry recheck."""
    return _window_clearance_obb(room, opening, rules)


def validate_room_change(
    document: SceneDocument,
    *,
    room_id: str,
    old_polygon: list[Vec2],
    new_polygon: list[Vec2],
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> None:
    """Re-check furniture affected by a room polygon edit.

    Shrinking or moving a wall can leave existing furniture crossing the new
    boundary even though the room polygon itself is valid. Every item that was
    in the old footprint, is in the new footprint, or is bound to the room must
    still fit inside its containing room's inset.
    """
    affected: list[Item] = []
    for item in document.items:
        position = (item.transform.position[0], item.transform.position[2])
        bound = item.constraint is not None and item.constraint.room_id == room_id
        if (
            bound
            or point_in_polygon(position, old_polygon)
            or point_in_polygon(position, new_polygon)
        ):
            affected.append(item)

    for item in affected:
        position = (item.transform.position[0], item.transform.position[2])
        room = _room_for_point(document, position)
        if room is None:
            raise GeometryError(
                "out_of_room",
                f"Room edit leaves item {item.id} outside every room.",
                "Move the furniture or restore the wall.",
                {"itemId": item.id, "roomId": room_id},
            )
        inset = inset_polygon(room.polygon, rules.room_inset)
        if not all(point_in_polygon(corner, inset) for corner in item_obb(item, catalog).corners()):
            raise GeometryError(
                "out_of_room",
                f"Room edit would push item {item.id} through a wall.",
                "Move the furniture inward before editing the room polygon.",
                {"itemId": item.id, "roomId": room.id, "position": list(position)},
            )


def validate_placement(
    document: SceneDocument,
    *,
    item_ids: set[str] | None = None,
    opening_ids: set[str] | None = None,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> None:
    """Validate only the items/openings touched by a commit.

    Existing fixture furniture is not re-litigated on every edit (some legacy
    layouts tuck chairs under tables), but every manual add/move and every new
    door is checked against the authoritative scene.
    """
    changed_items = [item for item in document.items if item_ids and item.id in item_ids]
    if changed_items:
        boxes = [(item, item_obb(item, catalog)) for item in document.items]
        by_id = {item.id: box for item, box in boxes}
        for changed in changed_items:
            position = (changed.transform.position[0], changed.transform.position[2])
            containing_room = _room_for_point(document, position)
            if containing_room is None:
                raise GeometryError(
                    "out_of_room",
                    f"Item {changed.id} is outside every room.",
                    "Place the item on a room floor.",
                    {"itemId": changed.id, "position": list(position)},
                )
            inset = inset_polygon(containing_room.polygon, rules.room_inset)
            changed_box = by_id[changed.id]
            if not all(point_in_polygon(corner, inset) for corner in changed_box.corners()):
                raise GeometryError(
                    "out_of_room",
                    f"Item {changed.id} crosses a wall or the room boundary.",
                    "Move it inward or rotate it so its footprint stays inside the walls.",
                    {"itemId": changed.id, "roomId": containing_room.id},
                )
            if item_allows_overlap(changed, catalog):
                continue
            changed_room = item_room_id(document, changed)
            for other, other_box in boxes:
                if other.id == changed.id:
                    continue
                if item_allows_overlap(other, catalog):
                    continue
                other_room = item_room_id(document, other)
                if (
                    changed_room is not None
                    and other_room is not None
                    and changed_room != other_room
                ):
                    continue
                if obb_overlap(changed_box, other_box, tolerance=COLLISION_TOLERANCE):
                    raise GeometryError(
                        "collision",
                        f"Items {changed.id} and {other.id} overlap.",
                        "Move one item, rotate it, or choose a smaller variant.",
                        {
                            "itemIds": [changed.id, other.id],
                            "kindIds": [changed.kind_id, other.kind_id],
                        },
                    )

    if changed_items:
        validate_window_clearance(
            document,
            item_ids={item.id for item in changed_items},
            catalog=catalog,
            rules=rules,
        )
        validate_walkways(
            document,
            item_ids={item.id for item in changed_items},
            catalog=catalog,
            rules=rules,
        )

    if not opening_ids and not changed_items:
        return
    rooms = {room.id: room for room in document.rooms}
    if changed_items:
        for opening in document.openings:
            if opening.type != "door":
                continue
            room = rooms.get(opening.room_id)
            if room is None:
                continue
            clearance = _opening_clearance_obb(room, opening, rules)
            if clearance is None:
                continue
            for item in changed_items:
                if item_allows_overlap(item, catalog):
                    continue
                if obb_overlap(item_obb(item, catalog), clearance, tolerance=COLLISION_TOLERANCE):
                    raise GeometryError(
                        "blocks_door",
                        f"Item {item.id} blocks the door {opening.id}.",
                        "Keep the door approach clear (at least 0.6 m deep).",
                        {"itemId": item.id, "openingId": opening.id},
                    )
    if not opening_ids:
        return
    for opening in document.openings:
        if opening.id not in opening_ids or opening.type != "door":
            continue
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        clearance = _opening_clearance_obb(room, opening, rules)
        if clearance is None:
            continue
        for item in document.items:
            if item_allows_overlap(item, catalog):
                continue
            if obb_overlap(item_obb(item, catalog), clearance, tolerance=COLLISION_TOLERANCE):
                raise GeometryError(
                    "blocks_door",
                    f"Item {item.id} blocks the door {opening.id}.",
                    "Keep the door approach clear (at least 0.6 m deep).",
                    {"itemId": item.id, "openingId": opening.id},
                )


def opening_clearance_obb(room: Room, opening: Opening, rules: Rules) -> OBB | None:
    """Public wrapper used by the solver spike and evaluation runner."""
    return _opening_clearance_obb(room, opening, rules)

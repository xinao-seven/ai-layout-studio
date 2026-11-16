"""Semantic placement for agent write tools (PRD §6.1).

Agent writes never contain raw coordinates: relations are resolved to
deterministic candidate transforms, validated through L1/L2/L3, and returned as
patch ops for a pending proposal. If every candidate fails, all refusals are
returned (capped at five) so the model can self-correct.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.agent.contracts import (
    AddItemInput,
    AgainstWallRelation,
    CornerRelation,
    FacingInput,
    FacingTowardInput,
    MoveItemInput,
    NearItemRelation,
    NearWindowRelation,
    Relation,
    UpdateItemInput,
)
from app.agent.refusals import dedupe_refusals, make_refusal, refusal_from_error
from app.agent.rules import check_item_rules
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import AnchorType, FacingDirection, FacingToward, ItemSource
from app.contracts.ids import IdPrefix, new_id, wall_segment_id
from app.contracts.patch import (
    ItemAddOp,
    ItemRemoveOp,
    ItemUpdateFields,
    ItemUpdateOp,
    PatchOp,
    TransformUpdate,
)
from app.contracts.proposal import Refusal
from app.contracts.rules import Rules
from app.contracts.scene import (
    Anchor,
    Clearance,
    FacingTowardSpec,
    Item,
    ItemConstraint,
    Near,
    Room,
    SceneDocument,
    Transform,
    Vec2,
)
from app.geometry.obb import OBB, obb_from_transform
from app.geometry.polygon import centroid, inset_polygon, point_in_polygon, polygon_bounds
from app.geometry.walls import derive_wall_segments, point_along_edge
from app.scene.document import CatalogMap
from app.scene.errors import SceneError
from app.scene.patch import apply_ops

DEFAULT_NEAR_GAP = 0.45


@dataclass(frozen=True)
class PlacementResult:
    """Either a validated ops list or the collected refusals."""

    ops: list[PatchOp] = field(default_factory=list)
    item: Item | None = None
    document_after: SceneDocument | None = None
    refusals: list[Refusal] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.ops) and not self.refusals


@dataclass(frozen=True)
class _WallInfo:
    edge_index: int
    a: Vec2
    b: Vec2
    normal: Vec2
    tangent: Vec2
    length: float
    label: str | None


def _find_room(document: SceneDocument, room_id: str) -> Room:
    for room in document.rooms:
        if room.id == room_id:
            return room
    raise SceneError(
        "unknown_room",
        f"Room {room_id!r} does not exist.",
        "Call query_scene to get the current room ids.",
        {"roomId": room_id},
    )


def _find_item(document: SceneDocument, item_id: str) -> Item:
    for item in document.items:
        if item.id == item_id:
            return item
    raise SceneError(
        "not_found",
        f"Item {item_id!r} does not exist.",
        "Call query_scene to get the latest item ids.",
        {"itemId": item_id},
    )


def _catalog_entry(catalog: CatalogMap, kind_id: str) -> CatalogEntry:
    entry = catalog.get(kind_id)
    if entry is None:
        raise SceneError(
            "unknown_kind",
            f"Unknown catalog kind {kind_id!r}.",
            "Call search_catalog with a keyword to find a valid kindId.",
            {"kindId": kind_id},
        )
    return entry


def _variant(entry: CatalogEntry, variant_id: str | None) -> tuple[str, Vec2, float]:
    chosen_id = variant_id or entry.default_variant_id
    for candidate in entry.variants:
        if candidate.id == chosen_id:
            return candidate.id, (candidate.size_m[0], candidate.size_m[1]), candidate.size_m[2]
    raise SceneError(
        "variant_missing",
        f"Kind {entry.kind_id!r} has no variant {chosen_id!r}.",
        "Use the default variant or search for a similar kind.",
        {"kindId": entry.kind_id, "variantId": chosen_id},
    )


def _item_size(item: Item, catalog: CatalogMap) -> tuple[Vec2, float]:
    entry = _catalog_entry(catalog, item.kind_id)
    _, size, height = _variant(entry, item.variant_id)
    return size, height


def _yaw_for_vector(vector: Vec2) -> float:
    return math.atan2(vector[0], vector[1])


def _direction_vector(direction: FacingDirection, north: Vec2) -> Vec2:
    nx, nz = north
    east = (-nz, nx)
    if direction == FacingDirection.NORTH:
        return (nx, nz)
    if direction == FacingDirection.SOUTH:
        return (-nx, -nz)
    if direction == FacingDirection.EAST:
        return east
    return (-east[0], -east[1])


def _opening_center(document: SceneDocument, opening_id: str) -> Vec2:
    for opening in document.openings:
        if opening.id != opening_id:
            continue
        room = next((room for room in document.rooms if room.id == opening.room_id), None)
        if room is None:
            break
        a = room.polygon[opening.edge_index]
        b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
        return point_along_edge(a, b, opening.offset)
    raise SceneError(
        "not_found",
        f"Opening {opening_id!r} does not exist.",
        "Call query_scene to get the current opening ids.",
        {"openingId": opening_id},
    )


def _facing_yaw(
    document: SceneDocument,
    facing: FacingInput | None,
    position: Vec2,
    default_yaw: float,
    *,
    exclude_item_id: str | None = None,
) -> float:
    if facing is None:
        return default_yaw
    if isinstance(facing, FacingDirection):
        return _yaw_for_vector(_direction_vector(facing, document.north))
    return _yaw_for_vector(
        _facing_target(
            document,
            facing,
            position,
            default_yaw=default_yaw,
            exclude_item_id=exclude_item_id,
        )
    )


def _facing_target(
    document: SceneDocument,
    facing: FacingTowardInput,
    position: Vec2,
    *,
    default_yaw: float,
    exclude_item_id: str | None,
) -> Vec2:
    if facing.toward == "item":
        candidates = [item for item in document.items if item.id != exclude_item_id]
        target = next((item for item in candidates if item.id == facing.id), None)
        if target is None and facing.id is None and candidates:
            target = min(
                candidates,
                key=lambda item: (
                    (item.transform.position[0] - position[0]) ** 2
                    + (item.transform.position[2] - position[1]) ** 2
                ),
            )
        if target is None:
            raise SceneError(
                "not_found",
                f"Facing target item {facing.id!r} does not exist.",
                "Call query_scene for item ids.",
            )
        centre = (target.transform.position[0], target.transform.position[2])
    else:
        openings = [
            opening
            for opening in document.openings
            if opening.type == facing.toward and (facing.id is None or opening.id == facing.id)
        ]
        if not openings:
            raise SceneError(
                "not_found",
                f"No {facing.toward} matches the facing target.",
                "Call query_scene for opening ids.",
            )
        target_opening = min(
            openings,
            key=lambda opening: (
                (_opening_center(document, opening.id)[0] - position[0]) ** 2
                + (_opening_center(document, opening.id)[1] - position[1]) ** 2
            ),
        )
        centre = _opening_center(document, target_opening.id)
    dx = centre[0] - position[0]
    dz = centre[1] - position[1]
    if abs(dx) <= 1e-9 and abs(dz) <= 1e-9:
        return (math.sin(default_yaw), math.cos(default_yaw))
    return (dx, dz)


def _wall_infos(document: SceneDocument, room: Room, relation: Relation) -> list[_WallInfo]:
    segments = derive_wall_segments(room, document.defaults)
    labels = room.wall_labels or {}
    infos = [
        _WallInfo(
            edge_index=segment.edge_index,
            a=segment.a,
            b=segment.b,
            normal=segment.normal,
            tangent=(
                (segment.b[0] - segment.a[0]) / segment.length,
                (segment.b[1] - segment.a[1]) / segment.length,
            ),
            length=segment.length,
            label=labels.get(str(segment.edge_index)),
        )
        for segment in segments
    ]
    if isinstance(relation, AgainstWallRelation):
        if relation.wall_id is not None:
            infos = [
                info
                for info in infos
                if wall_segment_id(room.id, info.edge_index) == relation.wall_id
            ]
            if not infos:
                raise SceneError(
                    "not_found",
                    f"Wall {relation.wall_id!r} is not part of room {room.id!r}.",
                    "Call query_scene for wall ids.",
                    {"wallId": relation.wall_id},
                )
        if relation.wall_label is not None:
            infos = [info for info in infos if info.label == relation.wall_label]
            if not infos:
                raise SceneError(
                    "not_found",
                    f"Wall label {relation.wall_label!r} does not exist in room {room.id!r}.",
                    "Call query_scene for the available wall labels.",
                    {"wallLabel": relation.wall_label},
                )
    return infos


def _wall_candidates(
    document: SceneDocument,
    room: Room,
    relation: Relation,
    *,
    size: Vec2,
    facing: FacingInput | None,
    rules: Rules,
    template: Item,
) -> tuple[list[Item], list[Refusal]]:
    infos = _wall_infos(document, room, relation)
    width, depth = size
    room_centre = centroid(room.polygon)
    candidates: list[tuple[float, int, Item]] = []
    refusals: list[Refusal] = []
    usable_start = rules.room_inset + width / 2.0
    usable_end: float
    for info in infos:
        usable_end = info.length - rules.room_inset - width / 2.0
        if usable_start > usable_end + 1e-9:
            refusals.append(
                make_refusal(
                    "item_too_large",
                    f"{template.kind_id} is wider than wall {info.edge_index + 1}.",
                    details={"edgeIndex": info.edge_index, "wallLength": info.length},
                )
            )
            continue
        offsets = [info.length / 2.0, usable_start, usable_end]
        step = 0.5
        current = usable_start
        while current <= usable_end + 1e-9:
            offsets.append(current)
            current += step
        seen_offsets: set[float] = set()
        for offset in offsets:
            if offset < usable_start - 1e-9 or offset > usable_end + 1e-9:
                continue
            rounded = round(offset, 6)
            if rounded in seen_offsets:
                continue
            seen_offsets.add(rounded)
            position = (
                info.a[0]
                + info.tangent[0] * offset
                + info.normal[0] * (rules.room_inset + depth / 2.0),
                info.a[1]
                + info.tangent[1] * offset
                + info.normal[1] * (rules.room_inset + depth / 2.0),
            )
            default_yaw = _yaw_for_vector(info.normal)
            yaw = _facing_yaw(document, facing, position, default_yaw, exclude_item_id=template.id)
            candidate = template.model_copy(
                update={
                    "transform": Transform(
                        position=(position[0], 0.0, position[1]), rotation_y=yaw
                    ),
                    "constraint": _constraint_for(
                        room.id,
                        relation,
                        wall_id=wall_segment_id(room.id, info.edge_index),
                        wall_label=info.label,
                        facing=facing,
                        clearance=template.constraint.clearance if template.constraint else None,
                    ),
                }
            )
            distance = (position[0] - room_centre[0]) ** 2 + (position[1] - room_centre[1]) ** 2
            candidates.append((distance, info.edge_index, candidate))
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return [candidate for _, _, candidate in candidates], refusals


def _obb_extent(item: Item, direction: Vec2, catalog: CatalogMap) -> float:
    size, _ = _item_size(item, catalog)
    box = obb_from_transform(item.transform.position, size, item.transform.rotation_y)
    axis_x, axis_z = box.axes()
    half_w, half_d = box.half_extents
    return (
        abs(direction[0] * axis_x[0] + direction[1] * axis_x[1]) * half_w
        + abs(direction[0] * axis_z[0] + direction[1] * axis_z[1]) * half_d
    )


def _near_item_candidates(
    document: SceneDocument,
    relation: NearItemRelation,
    *,
    size: Vec2,
    facing: FacingInput | None,
    catalog: CatalogMap,
    template: Item,
) -> list[Item]:
    reference = _find_item(document, relation.item_id)
    if reference.id == template.id:
        raise SceneError(
            "invalid_input",
            "An item cannot be placed near itself.",
            "Reference a different item id.",
        )
    centre = (reference.transform.position[0], reference.transform.position[2])
    front = (math.sin(reference.transform.rotation_y), math.cos(reference.transform.rotation_y))
    right = (front[1], -front[0])
    directions = [front, right, (-front[0], -front[1]), (-right[0], -right[1])]
    gap = relation.distance if relation.distance is not None else DEFAULT_NEAR_GAP
    template_box = obb_from_transform((0.0, 0.0, 0.0), size, reference.transform.rotation_y)
    axis_x, axis_z = template_box.axes()
    half_w, half_d = template_box.half_extents
    candidates: list[Item] = []
    for direction in directions:
        reference_extent = _obb_extent(reference, direction, catalog)
        new_extent = (
            abs(direction[0] * axis_x[0] + direction[1] * axis_x[1]) * half_w
            + abs(direction[0] * axis_z[0] + direction[1] * axis_z[1]) * half_d
        )
        position = (
            centre[0] + direction[0] * (reference_extent + gap + new_extent),
            centre[1] + direction[1] * (reference_extent + gap + new_extent),
        )
        yaw = _facing_yaw(
            document,
            facing,
            position,
            reference.transform.rotation_y,
            exclude_item_id=template.id,
        )
        candidates.append(
            template.model_copy(
                update={
                    "transform": Transform(
                        position=(position[0], 0.0, position[1]), rotation_y=yaw
                    ),
                    "constraint": _constraint_for(
                        template.constraint.room_id if template.constraint else None,
                        relation,
                        near_item_id=reference.id,
                        near_distance=gap,
                        facing=facing,
                        clearance=template.constraint.clearance if template.constraint else None,
                    ),
                }
            )
        )
    return candidates


def _corner_candidates(
    document: SceneDocument,
    room: Room,
    *,
    size: Vec2,
    facing: FacingInput | None,
    rules: Rules,
    template: Item,
    relation: Relation,
) -> list[Item]:
    inset = inset_polygon(room.polygon, rules.room_inset)
    centre = centroid(inset)
    candidates: list[Item] = []
    for vertex in inset:
        dx = centre[0] - vertex[0]
        dz = centre[1] - vertex[1]
        length = math.hypot(dx, dz)
        if length <= 1e-9:
            continue
        direction = (dx / length, dz / length)
        for distance in (0.25, 0.45, 0.65):
            position = (vertex[0] + direction[0] * distance, vertex[1] + direction[1] * distance)
            yaw = _facing_yaw(
                document,
                facing,
                position,
                _yaw_for_vector(direction),
                exclude_item_id=template.id,
            )
            candidates.append(
                template.model_copy(
                    update={
                        "transform": Transform(
                            position=(position[0], 0.0, position[1]), rotation_y=yaw
                        ),
                        "constraint": _constraint_for(
                            room.id,
                            relation,
                            facing=facing,
                            clearance=template.constraint.clearance
                            if template.constraint
                            else None,
                        ),
                    }
                )
            )
    candidates.sort(
        key=lambda candidate: (
            abs(candidate.transform.position[0] - centre[0])
            + abs(candidate.transform.position[2] - centre[1])
        )
    )
    return candidates


def _center_candidates(
    document: SceneDocument,
    room: Room,
    *,
    facing: FacingInput | None,
    template: Item,
    relation: Relation,
    size: Vec2,
) -> list[Item]:
    centre = centroid(room.polygon)
    offsets = [
        (0.0, 0.0),
        (0.25, 0.0),
        (-0.25, 0.0),
        (0.0, 0.25),
        (0.0, -0.25),
        (0.5, 0.5),
        (-0.5, 0.5),
        (0.5, -0.5),
        (-0.5, -0.5),
    ]
    candidates: list[Item] = []
    for dx, dz in offsets:
        position = (centre[0] + dx, centre[1] + dz)
        yaw = _facing_yaw(document, facing, position, 0.0, exclude_item_id=template.id)
        candidates.append(
            template.model_copy(
                update={
                    "transform": Transform(
                        position=(position[0], 0.0, position[1]), rotation_y=yaw
                    ),
                    "constraint": _constraint_for(
                        room.id,
                        relation,
                        facing=facing,
                        clearance=template.constraint.clearance if template.constraint else None,
                    ),
                }
            )
        )
    return candidates


def _near_window_candidates(
    document: SceneDocument,
    room: Room,
    relation: NearWindowRelation,
    *,
    size: Vec2,
    facing: FacingInput | None,
    rules: Rules,
    template: Item,
) -> list[Item]:
    openings = [
        opening
        for opening in document.openings
        if opening.room_id == room.id
        and opening.type == "window"
        and (relation.opening_id is None or opening.id == relation.opening_id)
    ]
    if not openings:
        raise SceneError(
            "not_found",
            "No matching window exists in this room.",
            "Call query_scene for window openings.",
            {"roomId": room.id, "openingId": relation.opening_id},
        )
    opening = openings[0]
    infos = [
        info
        for info in _wall_infos(document, room, relation)
        if info.edge_index == opening.edge_index
    ]
    candidates: list[Item] = []
    for info in infos:
        usable_start = rules.room_inset + size[0] / 2.0
        usable_end = info.length - rules.room_inset - size[0] / 2.0
        if usable_start > usable_end + 1e-9:
            continue
        offsets = [opening.offset]
        for step in (0.5, 1.0, 1.5, 2.0):
            offsets.extend([opening.offset - step, opening.offset + step])
        ordered = sorted(
            {round(offset, 6) for offset in offsets if usable_start <= offset <= usable_end},
            key=lambda offset: abs(offset - opening.offset),
        )
        for offset in ordered:
            position = (
                info.a[0]
                + info.tangent[0] * offset
                + info.normal[0] * (rules.room_inset + size[1] / 2.0),
                info.a[1]
                + info.tangent[1] * offset
                + info.normal[1] * (rules.room_inset + size[1] / 2.0),
            )
            yaw = _facing_yaw(
                document,
                facing,
                position,
                _yaw_for_vector(info.normal),
                exclude_item_id=template.id,
            )
            candidates.append(
                template.model_copy(
                    update={
                        "transform": Transform(
                            position=(position[0], 0.0, position[1]), rotation_y=yaw
                        ),
                        "constraint": _constraint_for(
                            room.id,
                            relation,
                            opening_id=opening.id,
                            facing=facing,
                            clearance=template.constraint.clearance
                            if template.constraint
                            else None,
                        ),
                    }
                )
            )
    return candidates


def _constraint_for(
    room_id: str | None,
    relation: Relation,
    *,
    wall_id: str | None = None,
    wall_label: str | None = None,
    opening_id: str | None = None,
    near_item_id: str | None = None,
    near_distance: float | None = None,
    facing: FacingInput | None = None,
    clearance: Clearance | None = None,
) -> ItemConstraint:
    anchor: Anchor | None = None
    near: Near | None = None
    if isinstance(relation, AgainstWallRelation):
        anchor = Anchor(type=AnchorType.WALL, wall_id=wall_id, wall_label=wall_label)
    elif isinstance(relation, NearWindowRelation):
        anchor = Anchor(type=AnchorType.WINDOW, opening_id=opening_id)
    elif isinstance(relation, CornerRelation):
        anchor = Anchor(type=AnchorType.CORNER)
    elif isinstance(relation, NearItemRelation):
        near = Near(item_id=near_item_id, distance=near_distance)
    facing_value: FacingDirection | FacingTowardSpec | None
    if facing is None:
        facing_value = None
    elif isinstance(facing, FacingDirection):
        facing_value = facing
    else:
        facing_value = FacingTowardSpec(toward=FacingToward(facing.toward), id=facing.id)
    return ItemConstraint(
        room_id=room_id,
        anchor=anchor,
        near=near,
        facing=facing_value,
        clearance=clearance,
    )


def _relation_candidates(
    document: SceneDocument,
    *,
    room: Room,
    relation: Relation,
    size: Vec2,
    facing: FacingInput | None,
    rules: Rules,
    catalog: CatalogMap,
    template: Item,
) -> tuple[list[Item], list[Refusal]]:
    if isinstance(relation, (AgainstWallRelation, NearWindowRelation)):
        if isinstance(relation, NearWindowRelation):
            return _near_window_candidates(
                document,
                room,
                relation,
                size=size,
                facing=facing,
                rules=rules,
                template=template,
            ), []
        return _wall_candidates(
            document,
            room,
            relation,
            size=size,
            facing=facing,
            rules=rules,
            template=template,
        )
    if isinstance(relation, NearItemRelation):
        return _near_item_candidates(
            document,
            relation,
            size=size,
            facing=facing,
            catalog=catalog,
            template=template,
        ), []
    if isinstance(relation, CornerRelation):
        return _corner_candidates(
            document,
            room,
            size=size,
            facing=facing,
            rules=rules,
            template=template,
            relation=relation,
        ), []
    return _center_candidates(
        document,
        room,
        facing=facing,
        template=template,
        relation=relation,
        size=size,
    ), []


def _try_candidates(
    document: SceneDocument,
    candidates: list[Item],
    *,
    item_id: str,
    catalog: CatalogMap,
    rules: Rules,
    strict: bool,
    as_update: bool,
) -> PlacementResult:
    refusals: list[Refusal] = []
    for candidate in candidates:
        if as_update:
            ops: list[PatchOp] = [
                ItemUpdateOp(
                    op="item.update",
                    item_id=candidate.id,
                    after=ItemUpdateFields(
                        transform=TransformUpdate(
                            position=candidate.transform.position,
                            rotation_y=candidate.transform.rotation_y,
                        ),
                        constraint=candidate.constraint,
                    ),
                )
            ]
        else:
            ops = [ItemAddOp(op="item.add", item=candidate)]
        try:
            after, _ = apply_ops(
                document,
                ops,
                validate=True,
                rules=rules,
                catalog=catalog,
            )
        except SceneError as error:
            refusals.append(
                refusal_from_error(error.code, error.message, error.suggest, error.details)
            )
            continue
        if strict:
            issues = check_item_rules(after, item_id=item_id, catalog=catalog, rules=rules)
            if issues:
                refusals.extend(
                    make_refusal(
                        "unrealistic",
                        issue.message,
                        suggest=issue.suggest,
                        details={"ruleId": issue.rule_id, "itemId": issue.item_id},
                    )
                    for issue in issues
                )
                continue
        return PlacementResult(ops=ops, item=candidate, document_after=after)
    if refusals:
        return PlacementResult(refusals=dedupe_refusals(refusals))
    return PlacementResult(
        refusals=[
            make_refusal(
                "no_space",
                "No valid placement satisfies the constraints.",
                details={"itemId": item_id},
            )
        ]
    )


def place_add(
    document: SceneDocument,
    request: AddItemInput,
    *,
    catalog: CatalogMap,
    rules: Rules,
    strict: bool = True,
) -> PlacementResult:
    """Resolve ``add_item`` into a validated pending item."""
    room = _find_room(document, request.room_id)
    entry = _catalog_entry(catalog, request.kind_id)
    variant_id, size, _ = _variant(entry, request.variant_id)
    min_x, min_z, max_x, max_z = polygon_bounds(room.polygon)
    available_x = max_x - min_x - 2 * rules.room_inset
    available_z = max_z - min_z - 2 * rules.room_inset
    if (
        min(size) > min(available_x, available_z) + 1e-9
        or max(size) > max(available_x, available_z) + 1e-9
    ):
        return PlacementResult(
            refusals=[
                make_refusal(
                    "item_too_large",
                    f"{entry.kind_id} does not fit inside room {room.id}.",
                    details={
                        "kindId": entry.kind_id,
                        "size": list(size),
                        "roomSpan": [available_x, available_z],
                    },
                )
            ]
        )
    template = Item(
        id=new_id(IdPrefix.ITEM),
        kind_id=entry.kind_id,
        variant_id=variant_id,
        transform=Transform(position=(0.0, 0.0, 0.0), rotation_y=0.0),
        constraint=ItemConstraint(room_id=room.id, clearance=request.clearance),
        source=ItemSource.AGENT,
    )
    candidates, refusals = _relation_candidates(
        document,
        room=room,
        relation=request.relation,
        size=size,
        facing=request.facing,
        rules=rules,
        catalog=catalog,
        template=template,
    )
    if not candidates:
        return PlacementResult(
            refusals=dedupe_refusals(refusals)
            or [make_refusal("no_space", "The relation produced no candidate positions.")]
        )
    result = _try_candidates(
        document,
        candidates,
        item_id=template.id,
        catalog=catalog,
        rules=rules,
        strict=strict,
        as_update=False,
    )
    if not result.ok and refusals:
        merged = dedupe_refusals([*result.refusals, *refusals])
        return PlacementResult(refusals=merged)
    return result


def place_move(
    document: SceneDocument,
    request: MoveItemInput,
    *,
    catalog: CatalogMap,
    rules: Rules,
    strict: bool = True,
) -> PlacementResult:
    """Resolve ``move_item`` into a validated item update."""
    item = _find_item(document, request.item_id)
    size, _ = _item_size(item, catalog)
    if request.offset is not None:
        position = (
            item.transform.position[0] + request.offset.dx,
            item.transform.position[1],
            item.transform.position[2] + request.offset.dz,
        )
        yaw = _facing_yaw(
            document,
            request.facing,
            (position[0], position[2]),
            item.transform.rotation_y,
            exclude_item_id=item.id,
        )
        candidate = item.model_copy(
            update={"transform": Transform(position=position, rotation_y=yaw)}
        )
        return _try_candidates(
            document,
            [candidate],
            item_id=item.id,
            catalog=catalog,
            rules=rules,
            strict=strict,
            as_update=True,
        )
    assert request.relation is not None
    fixture = item.constraint.room_id if item.constraint is not None else None
    room = (
        _find_room(document, fixture)
        if fixture is not None
        else _room_for_position(document, (item.transform.position[0], item.transform.position[2]))
    )
    template = item.model_copy(update={"constraint": ItemConstraint(room_id=room.id)})
    candidates, refusals = _relation_candidates(
        document,
        room=room,
        relation=request.relation,
        size=size,
        facing=request.facing,
        rules=rules,
        catalog=catalog,
        template=template,
    )
    if not candidates:
        return PlacementResult(
            refusals=dedupe_refusals(refusals)
            or [make_refusal("no_space", "The relation produced no candidate positions.")]
        )
    result = _try_candidates(
        document,
        candidates,
        item_id=item.id,
        catalog=catalog,
        rules=rules,
        strict=strict,
        as_update=True,
    )
    if not result.ok and refusals:
        return PlacementResult(refusals=dedupe_refusals([*result.refusals, *refusals]))
    return result


def place_update(
    document: SceneDocument,
    request: UpdateItemInput,
    *,
    catalog: CatalogMap,
    rules: Rules,
    strict: bool = True,
) -> PlacementResult:
    """Resolve ``update_item`` (variant/material) into an item update."""
    item = _find_item(document, request.item_id)
    if request.variant_id is not None:
        _catalog_entry(catalog, item.kind_id)
        _variant(_catalog_entry(catalog, item.kind_id), request.variant_id)
    after = ItemUpdateFields()
    if request.variant_id is not None:
        after = after.model_copy(update={"variant_id": request.variant_id})
    if request.material_overrides is not None:
        after = after.model_copy(update={"material_overrides": request.material_overrides})
    ops: list[PatchOp] = [ItemUpdateOp(op="item.update", item_id=item.id, after=after)]
    try:
        document_after, _ = apply_ops(document, ops, validate=True, rules=rules, catalog=catalog)
    except SceneError as error:
        return PlacementResult(
            refusals=[refusal_from_error(error.code, error.message, error.suggest, error.details)]
        )
    if strict:
        issues = check_item_rules(document_after, item_id=item.id, catalog=catalog, rules=rules)
        if issues:
            return PlacementResult(
                refusals=[
                    make_refusal(
                        "unrealistic",
                        issue.message,
                        suggest=issue.suggest,
                        details={"ruleId": issue.rule_id, "itemId": issue.item_id},
                    )
                    for issue in issues
                ]
            )
    updated = next(candidate for candidate in document_after.items if candidate.id == item.id)
    return PlacementResult(ops=ops, item=updated, document_after=document_after)


def place_remove(document: SceneDocument, item_id: str) -> PlacementResult:
    """Resolve ``remove_item`` into a reversible removal op."""
    item = _find_item(document, item_id)
    return PlacementResult(
        ops=[ItemRemoveOp(op="item.remove", item_id=item.id, before=item)],
        item=item,
    )


def _room_for_position(document: SceneDocument, position: Vec2) -> Room:
    for room in document.rooms:
        if point_in_polygon(position, room.polygon):
            return room
    raise SceneError(
        "out_of_room",
        "This item is outside every room.",
        "Move it into a room before using a relation.",
        {"position": list(position)},
    )


def item_placement_obb(item: Item, catalog: CatalogMap) -> OBB:
    size, _ = _item_size(item, catalog)
    return obb_from_transform(item.transform.position, size, item.transform.rotation_y)

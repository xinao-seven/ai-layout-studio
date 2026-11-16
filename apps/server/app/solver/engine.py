"""Deterministic multi-item layout solver (PRD §6.5, M4-01).

The solver is a pure function over the authoritative scene document, the
catalog and the shared rules. It never mutates the scene: callers turn the
returned placements into a pending proposal.

Design constraints from the PRD:

* P0 supports convex room polygons. Concave/orthogonal-L rooms return
  ``unsupported_room_shape`` instead of silently producing a bad layout.
* Candidate generation is deterministic (wall flush, corners, group rings and a
  coarse grid). No RNG is used; ties break on score then a coordinate signature.
* Hard constraints: room inset, OBB overlap, door clearance, window clearance,
  wardrobe front clearance and secondary passages between unrelated large items.
* Search is a bounded beam search (``SOLVER_BEAM_WIDTH``) with a wall clock and
  candidate-evaluation budget. ``allowPartials`` returns the best partial state.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.contracts.catalog import CatalogEntry
from app.contracts.layout import (
    AutoLayoutPlacement,
    AutoLayoutResult,
    AutoLayoutStats,
    AutoLayoutUnplaced,
)
from app.contracts.rules import Rules
from app.contracts.scene import (
    Anchor,
    ItemConstraint,
    Room,
    SceneDocument,
    Transform,
    Vec2,
)
from app.geometry.collision import (
    item_allows_overlap,
    item_height,
    item_obb,
    item_room_id,
)
from app.geometry.errors import GeometryError
from app.geometry.obb import OBB, obb_overlap
from app.geometry.openings import effective_sill_height, opening_center
from app.geometry.polygon import (
    centroid,
    inset_polygon,
    polygon_area,
    polygon_bounds,
)
from app.geometry.walls import derive_wall_segments
from app.scene.errors import SceneError

COLLISION_TOLERANCE = -0.005
WALL_STEP = 0.25
GRID_STEP = 0.5
MAX_CANDIDATES_PER_ITEM = 700
EPSILON = 1e-9


@dataclass(frozen=True)
class SolveSpec:
    """One requested item; ``existing_item_id`` is set by local replanning."""

    kind_id: str
    variant_id: str | None = None
    required: bool = True
    constraint: ItemConstraint | None = None
    existing_item_id: str | None = None


@dataclass(frozen=True)
class SolveOptions:
    allow_partial: bool = False
    seed: int = 0
    time_budget_ms: int = 5000
    max_evals: int = 50000
    replace_existing: bool = False


@dataclass(frozen=True)
class _WallRef:
    edge_index: int
    wall_id: str
    wall_label: str | None
    normal: Vec2


@dataclass(frozen=True)
class _Candidate:
    center: Vec2
    yaw: float
    wall: _WallRef | None = None
    corner: bool = False


@dataclass(frozen=True)
class _Obstacle:
    item_id: str
    kind_id: str
    box: OBB
    height: float
    allow_overlap: bool
    blocker: bool


@dataclass
class _Placement:
    spec: SolveSpec
    index: int
    item_id: str
    variant_id: str
    size: Vec2
    height: float
    center: Vec2
    yaw: float
    constraint: ItemConstraint | None
    candidate: _Candidate


@dataclass
class _State:
    placements: list[_Placement] = field(default_factory=list)
    obstacles: list[_Obstacle] = field(default_factory=list)
    score: float = 0.0

    def signature(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            (
                round(placement.center[0] * 1000),
                round(placement.center[1] * 1000),
                round(placement.yaw * 10000),
            )
            for placement in self.placements
        )


def _state_sort_key(
    state: _State, seed: int
) -> tuple[float, int, tuple[tuple[int, int, int], ...]]:
    signature = state.signature()
    flat = [value for entry in signature for value in entry]
    tie = (sum((index + 1) * value for index, value in enumerate(flat)) + seed) % 1_000_003
    return (-state.score, tie, signature)


def _is_convex(polygon: list[Vec2]) -> bool:
    if len(polygon) < 3:
        return False
    sign = 0
    count = len(polygon)
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        c = polygon[(index + 2) % count]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) <= EPSILON:
            continue
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif sign != current:
            return False
    return sign != 0


def _normalise_direction(vector: Vec2) -> Vec2:
    length = math.hypot(vector[0], vector[1])
    if length <= EPSILON:
        return (0.0, 1.0)
    return (vector[0] / length, vector[1] / length)


def _direction_vector(direction: str, north: Vec2) -> Vec2:
    nx, nz = north
    east = (-nz, nx)
    if direction == "north":
        return (nx, nz)
    if direction == "south":
        return (-nx, -nz)
    if direction == "east":
        return east
    return (-east[0], -east[1])


def _yaw_for(vector: Vec2) -> float:
    return math.atan2(vector[0], vector[1])


def _angle_delta(a: float, b: float) -> float:
    turn = math.pi * 2.0
    return abs((a - b + math.pi) % turn - math.pi)


def _allowed_rotations(rules: Rules) -> list[float]:
    return [math.radians(rotation) for rotation in rules.solver_rotations]


def _snap_yaw(yaw: float, rotations: list[float]) -> float:
    return min(rotations, key=lambda candidate: (_angle_delta(candidate, yaw), candidate))


def _spec_key(spec: SolveSpec, catalog: dict[str, CatalogEntry]) -> tuple[Any, ...]:
    entry = catalog.get(spec.kind_id)
    priority = entry.rules.anchor_priority if entry and entry.rules else 0
    variant = _variant(entry, spec.variant_id) if entry else None
    area = variant.size_m[0] * variant.size_m[1] if variant else 0.0
    return (0 if spec.required else 1, -priority, -area, spec.kind_id, spec.variant_id or "")


def _variant(entry: CatalogEntry, variant_id: str | None) -> Any:
    chosen = variant_id or entry.default_variant_id
    for candidate in entry.variants:
        if candidate.id == chosen:
            return candidate
    raise SceneError(
        "variant_missing",
        f"Kind {entry.kind_id!r} has no variant {chosen!r}.",
        "Use the default variant or search for a similar kind.",
        {"kindId": entry.kind_id, "variantId": chosen},
    )


def _entry_for(catalog: dict[str, CatalogEntry], kind_id: str) -> CatalogEntry:
    entry = catalog.get(kind_id)
    if entry is None:
        raise SceneError(
            "unknown_kind",
            f"Unknown catalog kind {kind_id!r}.",
            "Call search_catalog with a keyword to find a valid kindId.",
            {"kindId": kind_id},
        )
    return entry


def _is_blocker(entry: CatalogEntry | None) -> bool:
    if entry is None or (entry.rules and entry.rules.allow_overlap):
        return False
    if entry.category.value in {"sofa", "bed", "storage"}:
        return True
    return entry.kind_id.startswith(("wardrobe", "tv_stand", "kitchen_", "fridge", "bed_"))


def _catalog_allows_overlap(catalog: dict[str, CatalogEntry], kind_id: str) -> bool:
    entry = catalog.get(kind_id)
    return bool(entry is not None and entry.rules is not None and entry.rules.allow_overlap)


def _are_grouped(first: CatalogEntry | None, second: CatalogEntry | None) -> bool:
    if first is None or second is None:
        return False
    first_group = set(first.rules.group_with) if first.rules else set()
    second_group = set(second.rules.group_with) if second.rules else set()
    return second.kind_id in first_group or first.kind_id in second_group


def _inside_convex(point: Vec2, polygon: list[Vec2]) -> bool:
    """Fast point-in-convex test for the inset polygon (no Shapely)."""
    orientation = 0
    count = len(polygon)
    x, z = point
    for index in range(count):
        ax, az = polygon[index]
        bx, bz = polygon[(index + 1) % count]
        cross = (bx - ax) * (z - az) - (bz - az) * (x - ax)
        if abs(cross) <= 1e-9:
            continue
        current = 1 if cross > 0 else -1
        if orientation == 0:
            orientation = current
        elif orientation != current:
            return False
    return True


def _point_segment_distance(point: Vec2, a: Vec2, b: Vec2) -> float:
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length_sq = dx * dx + dz * dz
    if length_sq <= 1e-12:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length_sq
    t = max(0.0, min(1.0, t))
    closest = (a[0] + dx * t, a[1] + dz * t)
    return math.hypot(point[0] - closest[0], point[1] - closest[1])


def _polygon_distance(box: OBB, other: OBB) -> float:
    """Analytic OBB gap used by walkway and group scoring."""
    if obb_overlap(box, other, tolerance=COLLISION_TOLERANCE):
        return 0.0
    first = box.corners()
    second = other.corners()
    best = math.inf
    for corners, edges in ((first, second), (second, first)):
        for point in corners:
            for index in range(4):
                distance = _point_segment_distance(point, edges[index], edges[(index + 1) % 4])
                if distance < best:
                    best = distance
    return best


def _box_inside(center: Vec2, size: Vec2, yaw: float, inset: list[Vec2]) -> bool:
    return all(_inside_convex(corner, inset) for corner in OBB(center, size, yaw).corners())


def _extent(size: Vec2, yaw: float, direction: Vec2) -> float:
    axis_x = (math.cos(yaw), -math.sin(yaw))
    axis_z = (math.sin(yaw), math.cos(yaw))
    return abs(size[0] / 2.0 * (axis_x[0] * direction[0] + axis_x[1] * direction[1])) + abs(
        size[1] / 2.0 * (axis_z[0] * direction[0] + axis_z[1] * direction[1])
    )


def _cap(items: list[_Candidate], limit: int) -> list[_Candidate]:
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:1]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(index * step)] for index in range(limit)]


def _dedupe(candidates: list[_Candidate]) -> list[_Candidate]:
    seen: set[tuple[int, int, int]] = set()
    result: list[_Candidate] = []
    for candidate in candidates:
        key = (
            round(candidate.center[0] * 1000),
            round(candidate.center[1] * 1000),
            round(candidate.yaw * 10000),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _door_boxes(document: SceneDocument, room: Room, rules: Rules) -> list[OBB]:
    boxes: list[OBB] = []
    for opening in document.openings:
        if opening.type != "door" or opening.room_id != room.id:
            continue
        if opening.edge_index >= len(room.polygon):
            continue
        a = room.polygon[opening.edge_index]
        b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length <= EPSILON:
            continue
        tangent = (dx / length, dz / length)
        width = max(opening.width, rules.door_approach_width)
        boxes.append(
            OBB(
                center=opening_center(room, opening),
                size=(width, rules.door_clear_depth * 2.0),
                yaw=math.atan2(-tangent[1], tangent[0]),
            )
        )
    return boxes


def _window_boxes(document: SceneDocument, room: Room, rules: Rules) -> list[OBB]:
    boxes: list[OBB] = []
    for opening in document.openings:
        if opening.type != "window" or opening.room_id != room.id:
            continue
        if opening.edge_index >= len(room.polygon):
            continue
        a = room.polygon[opening.edge_index]
        b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length <= EPSILON:
            continue
        tangent = (dx / length, dz / length)
        normal = (-tangent[1], tangent[0])
        thickness = room.wall_thickness if room.wall_thickness is not None else rules.wall_thickness
        offset = thickness / 2.0 + rules.window_clear_depth / 2.0
        center = opening_center(room, opening)
        inside = (center[0] + normal[0] * offset, center[1] + normal[1] * offset)
        boxes.append(
            OBB(
                center=inside,
                size=(opening.width, rules.window_clear_depth),
                yaw=math.atan2(-tangent[1], tangent[0]),
            )
        )
    return boxes


def _front_clearance(item_box: OBB, kind_id: str, height: float) -> OBB | None:
    if not (kind_id.startswith("wardrobe") or height >= 1.5):
        return None
    if height < 1.5:
        return None
    (axis_x, axis_z) = item_box.axes()
    front = (axis_z[0], axis_z[1])
    center = (
        item_box.center[0] + front[0] * (item_box.size[1] / 2.0 + 0.3),
        item_box.center[1] + front[1] * (item_box.size[1] / 2.0 + 0.3),
    )
    return OBB(center=center, size=(item_box.size[0], 0.6), yaw=item_box.yaw)


def _target_center(
    document: SceneDocument, room: Room, constraint: ItemConstraint | None
) -> Vec2 | None:
    if constraint is None or constraint.near is None:
        return None
    near = constraint.near
    if near.item_id is not None:
        for item in document.items:
            if item.id == near.item_id:
                return (item.transform.position[0], item.transform.position[2])
        return None
    if near.opening_id is not None:
        for opening in document.openings:
            if opening.id == near.opening_id and opening.room_id == room.id:
                return opening_center(room, opening)
    return None


def _facing_target(
    document: SceneDocument,
    room: Room,
    constraint: ItemConstraint | None,
    position: Vec2,
) -> Vec2 | None:
    facing = constraint.facing if constraint is not None else None
    if facing is None or isinstance(facing, str):
        return None
    if facing.toward == "item":
        for item in document.items:
            if facing.id is None or item.id == facing.id:
                return (item.transform.position[0], item.transform.position[2])
        return None
    candidates = [
        opening
        for opening in document.openings
        if opening.type.value == facing.toward.value
        and opening.room_id == room.id
        and (facing.id is None or opening.id == facing.id)
    ]
    if not candidates:
        return None
    target = min(
        candidates,
        key=lambda opening: (
            (opening_center(room, opening)[0] - position[0]) ** 2
            + (opening_center(room, opening)[1] - position[1]) ** 2,
            opening.id,
        ),
    )
    return opening_center(room, target)


def _wall_candidates(
    document: SceneDocument,
    room: Room,
    size: Vec2,
    constraint: ItemConstraint | None,
    rules: Rules,
    insets: list[Vec2],
) -> list[_Candidate]:
    segments = derive_wall_segments(room, document.defaults)
    labels = room.wall_labels or {}
    allowed_edges: set[int] | None = None
    anchor = constraint.anchor if constraint is not None else None
    if anchor is not None:
        if anchor.wall_id is not None:
            allowed_edges = {
                segment.edge_index for segment in segments if segment.id == anchor.wall_id
            }
        if anchor.wall_label is not None:
            labelled = {
                segment.edge_index
                for segment in segments
                if labels.get(str(segment.edge_index)) == anchor.wall_label
            }
            allowed_edges = labelled if allowed_edges is None else allowed_edges & labelled
    rotations = _allowed_rotations(rules)
    facing = constraint.facing if constraint is not None else None
    desired_yaw: float | None = None
    if isinstance(facing, str):
        desired_yaw = _yaw_for(_direction_vector(facing, document.north))

    candidates: list[_Candidate] = []
    for segment in segments:
        if allowed_edges is not None and segment.edge_index not in allowed_edges:
            continue
        normal = segment.normal
        tangent = (
            (segment.b[0] - segment.a[0]) / segment.length,
            (segment.b[1] - segment.a[1]) / segment.length,
        )
        aligned_yaw = _yaw_for(normal)
        if desired_yaw is not None:
            if _angle_delta(desired_yaw, aligned_yaw) > 1e-6:
                # A wall-flush pose must keep the item's back against the wall;
                # a fixed facing direction only fits the wall it faces away from.
                continue
            yaws = [desired_yaw]
        else:
            yaws = [_snap_yaw(aligned_yaw, rotations)] if rotations else [aligned_yaw]
        for yaw in yaws:
            extent_n = _extent(size, yaw, normal)
            extent_t = _extent(size, yaw, tangent)
            low = extent_t
            high = segment.length - extent_t
            if low > high + EPSILON:
                continue
            offsets = [low, high, (low + high) / 2.0]
            current = low
            while current <= high + EPSILON:
                offsets.append(current)
                current += WALL_STEP
            per_edge: list[_Candidate] = []
            for offset in sorted(set(round(value, 6) for value in offsets)):
                if offset < low - EPSILON or offset > high + EPSILON:
                    continue
                center = (
                    segment.a[0] + tangent[0] * offset + normal[0] * (extent_n + rules.room_inset),
                    segment.a[1] + tangent[1] * offset + normal[1] * (extent_n + rules.room_inset),
                )
                if not _box_inside(center, size, yaw, insets):
                    continue
                per_edge.append(
                    _Candidate(
                        center=center,
                        yaw=yaw,
                        wall=_WallRef(
                            edge_index=segment.edge_index,
                            wall_id=segment.id,
                            wall_label=labels.get(str(segment.edge_index)),
                            normal=normal,
                        ),
                    )
                )
            candidates.extend(_cap(sorted(per_edge, key=lambda item: (item.center, item.yaw)), 16))
    return candidates


def _grid_candidates(
    size: Vec2,
    rules: Rules,
    insets: list[Vec2],
    *,
    step: float = GRID_STEP,
) -> list[_Candidate]:
    min_x, min_z, max_x, max_z = polygon_bounds(insets)
    rotations = _allowed_rotations(rules)
    candidates: list[_Candidate] = []
    x = min_x
    while x <= max_x + EPSILON:
        z = min_z
        while z <= max_z + EPSILON:
            for yaw in rotations:
                if _box_inside((x, z), size, yaw, insets):
                    candidates.append(_Candidate(center=(x, z), yaw=yaw))
            z += step
        x += step
    return _cap(sorted(candidates, key=lambda item: (item.center, item.yaw)), 260)


def _corner_candidates(
    document: SceneDocument,
    room: Room,
    size: Vec2,
    rules: Rules,
    insets: list[Vec2],
    constraint: ItemConstraint | None,
) -> list[_Candidate]:
    min_x, min_z, max_x, max_z = polygon_bounds(insets)
    rotations = _allowed_rotations(rules)
    facing = constraint.facing if constraint is not None else None
    yaws = rotations
    if isinstance(facing, str):
        yaws = [_yaw_for(_direction_vector(facing, document.north))]
    candidates: list[_Candidate] = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            for yaw in yaws:
                extent_x = _extent(size, yaw, (1.0, 0.0))
                extent_z = _extent(size, yaw, (0.0, 1.0))
                center = (
                    (min_x + extent_x) if sx < 0 else (max_x - extent_x),
                    (min_z + extent_z) if sz < 0 else (max_z - extent_z),
                )
                if _box_inside(center, size, yaw, insets):
                    candidates.append(
                        _Candidate(
                            center=center,
                            yaw=yaw,
                            wall=None,
                            corner=True,
                        )
                    )
    return _dedupe(candidates)


def _near_candidates(
    document: SceneDocument,
    room: Room,
    size: Vec2,
    constraint: ItemConstraint | None,
    rules: Rules,
    insets: list[Vec2],
) -> list[_Candidate]:
    target = _target_center(document, room, constraint)
    if target is None:
        return []
    rotations = _allowed_rotations(rules)
    candidates: list[_Candidate] = []
    for gap in (0.45, 0.75, 1.0):
        for direction in (
            (1.0, 0.0),
            (-1.0, 0.0),
            (0.0, 1.0),
            (0.0, -1.0),
            (0.7071, 0.7071),
            (0.7071, -0.7071),
            (-0.7071, 0.7071),
            (-0.7071, -0.7071),
        ):
            for yaw in rotations:
                extent = _extent(size, yaw, direction)
                center = (
                    target[0] + direction[0] * (extent + gap),
                    target[1] + direction[1] * (extent + gap),
                )
                if _box_inside(center, size, yaw, insets):
                    candidates.append(_Candidate(center=center, yaw=yaw))
    return _dedupe(sorted(candidates, key=lambda item: (item.center, item.yaw)))


def _group_candidates(
    spec: SolveSpec,
    size: Vec2,
    catalog: dict[str, CatalogEntry],
    placed: list[_Placement],
) -> list[_Candidate]:
    """Exact-gap poses around already-placed group partners.

    Static wall/grid candidates are spaced 0.25–0.5 m apart, which can miss the
    catalog's recommended group gap (for example a coffee table 0.3–0.6 m from
    a sofa). These candidates are generated from the anchor footprint instead.
    """
    entry = catalog.get(spec.kind_id)
    candidates: list[_Candidate] = []
    for placement in placed:
        other_entry = catalog.get(placement.spec.kind_id)
        if not _are_grouped(entry, other_entry):
            continue
        anchor = OBB(placement.center, placement.size, placement.yaw)
        axis_x, axis_z = anchor.axes()
        for direction in (
            axis_z,
            axis_x,
            (-axis_z[0], -axis_z[1]),
            (-axis_x[0], -axis_x[1]),
        ):
            anchor_extent = _extent(placement.size, placement.yaw, direction)
            new_extent = _extent(size, placement.yaw, direction)
            for gap in (0.3, 0.45, 0.6):
                distance = anchor_extent + gap + new_extent
                candidates.append(
                    _Candidate(
                        center=(
                            placement.center[0] + direction[0] * distance,
                            placement.center[1] + direction[1] * distance,
                        ),
                        yaw=placement.yaw,
                    )
                )
    return _dedupe(candidates)


def _anchor_opening_center(
    document: SceneDocument, room: Room, constraint: ItemConstraint | None
) -> Vec2 | None:
    if constraint is None or constraint.anchor is None:
        return None
    opening_id = constraint.anchor.opening_id
    if opening_id is None:
        return None
    opening = next(
        (
            candidate
            for candidate in document.openings
            if candidate.id == opening_id and candidate.room_id == room.id
        ),
        None,
    )
    return opening_center(room, opening) if opening is not None else None


def _candidates_for(
    document: SceneDocument,
    room: Room,
    spec: SolveSpec,
    size: Vec2,
    rules: Rules,
    insets: list[Vec2],
    entry: CatalogEntry,
) -> list[_Candidate]:
    must_wall = bool(entry.rules and entry.rules.must_against_wall)
    anchor = spec.constraint.anchor if spec.constraint is not None else None
    anchor_type = anchor.type if anchor is not None else None
    wall = _wall_candidates(
        document,
        room,
        size,
        spec.constraint,
        rules,
        insets,
    )
    corner = (
        _corner_candidates(document, room, size, rules, insets, spec.constraint)
        if anchor_type == "corner"
        else []
    )
    grid = _grid_candidates(size, rules, insets) if anchor_type is None and not must_wall else []
    near = _near_candidates(document, room, size, spec.constraint, rules, insets)
    if anchor_type == "wall":
        candidates = wall
    elif anchor_type == "corner":
        candidates = corner or wall
    elif anchor_type in {"window", "door"}:
        candidates = wall or grid or near
    else:
        candidates = [*wall, *corner, *near, *grid]
    if anchor_type in {"window", "door"}:
        target = _anchor_opening_center(document, room, spec.constraint)
        if target is not None:
            candidates = [
                candidate
                for candidate in candidates
                if math.hypot(candidate.center[0] - target[0], candidate.center[1] - target[1])
                <= 2.0
            ]
    facing = spec.constraint.facing if spec.constraint is not None else None
    if isinstance(facing, str):
        desired = _yaw_for(_direction_vector(facing, document.north))
        candidates = [
            candidate for candidate in candidates if _angle_delta(candidate.yaw, desired) <= 1e-6
        ]
    near_target = _target_center(document, room, spec.constraint)
    if near_target is not None:
        candidates = [
            candidate
            for candidate in candidates
            if math.hypot(
                candidate.center[0] - near_target[0],
                candidate.center[1] - near_target[1],
            )
            <= 2.5
        ]
    return _cap(
        sorted(_dedupe(candidates), key=lambda item: (item.yaw, item.center)),
        MAX_CANDIDATES_PER_ITEM,
    )


def _validate_constraint_targets(document: SceneDocument, room: Room, spec: SolveSpec) -> None:
    constraint = spec.constraint
    if constraint is None:
        return
    if constraint.anchor is not None:
        anchor = constraint.anchor
        segments = derive_wall_segments(room, document.defaults)
        if anchor.wall_id is not None and all(segment.id != anchor.wall_id for segment in segments):
            raise SceneError(
                "not_found",
                f"Wall {anchor.wall_id!r} is not part of room {room.id!r}.",
                "Call query_scene for the current wall ids.",
                {"wallId": anchor.wall_id, "roomId": room.id},
            )
        labels = room.wall_labels or {}
        if anchor.wall_label is not None and anchor.wall_label not in labels.values():
            raise SceneError(
                "not_found",
                f"Wall label {anchor.wall_label!r} does not exist in room {room.id!r}.",
                "Call query_scene for the available wall labels.",
                {"wallLabel": anchor.wall_label, "roomId": room.id},
            )
        if anchor.opening_id is not None and all(
            opening.id != anchor.opening_id or opening.room_id != room.id
            for opening in document.openings
        ):
            raise SceneError(
                "not_found",
                f"Opening {anchor.opening_id!r} is not part of room {room.id!r}.",
                "Call query_scene for the current opening ids.",
                {"openingId": anchor.opening_id, "roomId": room.id},
            )
    if (
        constraint.near is not None
        and constraint.near.item_id is not None
        and all(item.id != constraint.near.item_id for item in document.items)
    ):
        raise SceneError(
            "not_found",
            f"Near target {constraint.near.item_id!r} does not exist.",
            "Call query_scene for the current item ids.",
            {"itemId": constraint.near.item_id},
        )


def _apply_facing(
    document: SceneDocument,
    room: Room,
    constraint: ItemConstraint | None,
    candidate: _Candidate,
    rules: Rules,
) -> _Candidate:
    facing = constraint.facing if constraint is not None else None
    if facing is None or isinstance(facing, str):
        return candidate
    target = _facing_target(document, room, constraint, candidate.center)
    if target is None:
        return candidate
    dx = target[0] - candidate.center[0]
    dz = target[1] - candidate.center[1]
    if abs(dx) <= EPSILON and abs(dz) <= EPSILON:
        return candidate
    snapped = _snap_yaw(_yaw_for((dx, dz)), _allowed_rotations(rules))
    return _Candidate(
        center=candidate.center, yaw=snapped, wall=candidate.wall, corner=candidate.corner
    )


def _existing_obstacles(
    document: SceneDocument,
    room: Room,
    catalog: dict[str, CatalogEntry],
    *,
    exclude_ids: set[str],
) -> list[_Obstacle]:
    obstacles: list[_Obstacle] = []
    for item in document.items:
        if item.id in exclude_ids:
            continue
        if item_room_id(document, item) != room.id:
            continue
        entry = catalog.get(item.kind_id)
        obstacles.append(
            _Obstacle(
                item_id=item.id,
                kind_id=item.kind_id,
                box=item_obb(item, catalog),
                height=item_height(item, catalog),
                allow_overlap=item_allows_overlap(item, catalog),
                blocker=_is_blocker(entry),
            )
        )
    return obstacles


def _front_zones(obstacles: list[_Obstacle]) -> list[OBB]:
    zones: list[OBB] = []
    for obstacle in obstacles:
        if obstacle.allow_overlap:
            continue
        zone = _front_clearance(obstacle.box, obstacle.kind_id, obstacle.height)
        if zone is not None:
            zones.append(zone)
    return zones


def _candidate_score(
    document: SceneDocument,
    room: Room,
    spec: SolveSpec,
    candidate: _Candidate,
    box: OBB,
    placed: list[_Placement],
    obstacles: list[_Obstacle],
    catalog: dict[str, CatalogEntry],
    rules: Rules,
    room_centre: Vec2,
    door_boxes: list[OBB],
) -> float:
    score = 0.0
    entry = catalog.get(spec.kind_id)
    distance_centre = math.hypot(
        candidate.center[0] - room_centre[0], candidate.center[1] - room_centre[1]
    )
    score -= 0.08 * distance_centre
    if entry is not None and entry.rules and entry.rules.must_against_wall:
        if candidate.wall is None:
            score -= 6.0
        elif entry.rules.preferred_wall is not None and isinstance(
            spec.constraint.facing if spec.constraint is not None else None, str
        ):
            desired = _yaw_for(_direction_vector(entry.rules.preferred_wall.value, document.north))
            score -= 4.0 * min(1.0, _angle_delta(desired, candidate.yaw) / math.pi)
    placed_kinds = [placement.spec.kind_id for placement in placed]
    for placement in placed:
        other_box = OBB(placement.center, placement.size, placement.yaw)
        other_entry = catalog.get(placement.spec.kind_id)
        if _are_grouped(entry, other_entry):
            gap = _polygon_distance(box, other_box)
            if gap < 0.25:
                score += 2.0
            elif gap <= 1.0:
                score += 4.0 - abs(gap - 0.55) * 2.0
            else:
                score -= 0.5 * (gap - 1.0)
        if spec.kind_id.startswith("nightstand") and placement.spec.kind_id.startswith("bed_"):
            gap = _polygon_distance(box, other_box)
            score += 5.0 if gap <= 0.6 else -4.0
    del placed_kinds
    for door in door_boxes:
        gap = _polygon_distance(box, door)
        if gap < 0.7:
            score -= (0.7 - gap) * 8.0
    for obstacle in obstacles:
        if obstacle.allow_overlap or not obstacle.blocker:
            continue
        other_entry = catalog.get(obstacle.kind_id)
        if _are_grouped(entry, other_entry):
            continue
        gap = _polygon_distance(box, obstacle.box)
        if rules.passage_secondary <= gap <= 0.8:
            score -= 2.0
    near_target = _target_center(document, room, spec.constraint)
    if near_target is not None:
        wanted = spec.constraint.near.distance if spec.constraint and spec.constraint.near else None
        target_gap = wanted if wanted is not None else 0.45
        distance = math.hypot(
            candidate.center[0] - near_target[0],
            candidate.center[1] - near_target[1],
        )
        score -= max(0.0, distance - (target_gap + 1.2)) * 4.0
    return score


def _size_for(placement: _Placement, catalog: dict[str, CatalogEntry]) -> Vec2:
    del catalog
    return placement.size


def _feasible(
    document: SceneDocument,
    room: Room,
    spec: SolveSpec,
    candidate: _Candidate,
    size: Vec2,
    height: float,
    insets: list[Vec2],
    state: _State,
    catalog: dict[str, CatalogEntry],
    rules: Rules,
    door_boxes: list[OBB],
    window_pairs: list[tuple[OBB, float]],
    front_zones: list[OBB],
) -> bool:
    if not _box_inside(candidate.center, size, candidate.yaw, insets):
        return False
    box = OBB(candidate.center, size, candidate.yaw)
    if _catalog_allows_overlap(catalog, spec.kind_id):
        # Rugs and table-top decor are exempt from the placement constraints,
        # matching validate_placement's allowOverlap semantics.
        return True
    entry = catalog.get(spec.kind_id)
    if entry is not None and entry.rules and entry.rules.must_against_wall:
        if candidate.wall is None:
            return False
        aligned = _yaw_for(candidate.wall.normal)
        if _angle_delta(candidate.yaw, aligned) > 1e-6:
            return False
    if any(obb_overlap(box, door, tolerance=COLLISION_TOLERANCE) for door in door_boxes):
        return False
    for window, sill in window_pairs:
        if height <= sill + 1e-6:
            continue
        if obb_overlap(box, window, tolerance=COLLISION_TOLERANCE):
            return False
    placed_zones = (
        _front_zones(state.obstacles[-len(state.placements) :]) if state.placements else []
    )
    for zone in [*front_zones, *placed_zones]:
        if obb_overlap(box, zone, tolerance=COLLISION_TOLERANCE):
            return False
    if entry is not None:
        front_zone = _front_clearance(box, spec.kind_id, height)
        if front_zone is not None:
            if not _box_inside(front_zone.center, front_zone.size, front_zone.yaw, insets):
                return False
            if any(
                obb_overlap(front_zone, door, tolerance=COLLISION_TOLERANCE) for door in door_boxes
            ):
                return False
            for obstacle in state.obstacles:
                if obstacle.allow_overlap:
                    continue
                if obb_overlap(front_zone, obstacle.box, tolerance=COLLISION_TOLERANCE):
                    return False
    for obstacle in state.obstacles:
        if obstacle.allow_overlap:
            continue
        if not obb_overlap(box, obstacle.box, tolerance=COLLISION_TOLERANCE):
            gap = _polygon_distance(box, obstacle.box)
            if (
                gap > 1e-6
                and gap < rules.passage_secondary - 1e-6
                and obstacle.blocker
                and _is_blocker(catalog.get(spec.kind_id))
                and not _are_grouped(catalog.get(spec.kind_id), catalog.get(obstacle.kind_id))
            ):
                return False
            continue
        return False
    if spec.kind_id.startswith("nightstand"):
        beds = [
            OBB(placement.center, placement.size, placement.yaw)
            for placement in state.placements
            if placement.spec.kind_id.startswith("bed_")
        ]
        beds.extend(
            obstacle.box
            for obstacle in state.obstacles
            if obstacle.kind_id.startswith("bed_") and not obstacle.allow_overlap
        )
        if beds:
            nearest = min(_polygon_distance(box, bed) for bed in beds)
            if nearest > 0.6 + 1e-6:
                return False
    del document, room
    return True


def _make_constraint(
    spec: SolveSpec,
    room: Room,
    candidate: _Candidate,
) -> ItemConstraint | None:
    if spec.constraint is None:
        if candidate.wall is None and not candidate.corner:
            return ItemConstraint(room_id=room.id)
        if candidate.corner:
            return ItemConstraint(room_id=room.id, anchor=Anchor(type="corner"))
        assert candidate.wall is not None
        return ItemConstraint(
            room_id=room.id,
            anchor=Anchor(
                type="wall",
                wall_id=candidate.wall.wall_id,
                wall_label=candidate.wall.wall_label,
            ),
        )
    constraint = spec.constraint.model_copy(deep=True)
    constraint.room_id = room.id
    if candidate.corner and constraint.anchor is None:
        constraint.anchor = Anchor(type="corner")
    elif candidate.wall is not None and constraint.anchor is None:
        constraint.anchor = Anchor(
            type="wall",
            wall_id=candidate.wall.wall_id,
            wall_label=candidate.wall.wall_label,
        )
    return constraint


def _stable_item_id(document: SceneDocument, room: Room, spec: SolveSpec, index: int) -> str:
    if spec.existing_item_id is not None:
        return spec.existing_item_id
    raw = f"{document.id}|{room.id}|{spec.kind_id}|{spec.variant_id or ''}|{index}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    used = {item.id for item in document.items}
    candidate = f"it_auto_{digest}_{index}"
    suffix = 0
    while candidate in used:
        suffix += 1
        candidate = f"it_auto_{digest}_{index}_{suffix}"
    return candidate


def solve_layout(
    document: SceneDocument,
    catalog: dict[str, CatalogEntry],
    rules: Rules,
    *,
    room_id: str,
    items: list[SolveSpec],
    options: SolveOptions | None = None,
) -> AutoLayoutResult:
    """Solve a room layout deterministically (pure function)."""
    started = time.perf_counter()
    options = options or SolveOptions()
    room = next((candidate for candidate in document.rooms if candidate.id == room_id), None)
    if room is None:
        raise SceneError(
            "unknown_room",
            f"Room {room_id!r} does not exist.",
            "Call query_scene to get the current room ids.",
            {"roomId": room_id},
        )
    if not _is_convex(room.polygon):
        raise SceneError(
            "unsupported_room_shape",
            f"Room {room_id!r} is not convex.",
            "Use manual placement plus verify_layout, or split the room.",
            {"roomId": room_id},
        )
    if len(items) > rules.auto_layout_max_items:
        raise SceneError(
            "item_too_large",
            f"auto_layout accepts at most {rules.auto_layout_max_items} items.",
            "Reduce the item list or split the room.",
            {"roomId": room_id, "count": len(items)},
        )
    insets = inset_polygon(room.polygon, rules.room_inset)
    inset_area = polygon_area(insets)
    room_centre = centroid(room.polygon)
    door_boxes = _door_boxes(document, room, rules)
    # Build window strips with their sill heights so tall items cannot block them.
    window_pairs: list[tuple[OBB, float]] = []
    for opening in document.openings:
        if opening.type != "window" or opening.room_id != room.id:
            continue
        a = room.polygon[opening.edge_index]
        b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length <= EPSILON:
            continue
        tangent = (dx / length, dz / length)
        normal = (-tangent[1], tangent[0])
        thickness = room.wall_thickness if room.wall_thickness is not None else rules.wall_thickness
        offset = thickness / 2.0 + rules.window_clear_depth / 2.0
        centre = opening_center(room, opening)
        window_pairs.append(
            (
                OBB(
                    center=(centre[0] + normal[0] * offset, centre[1] + normal[1] * offset),
                    size=(opening.width, rules.window_clear_depth),
                    yaw=math.atan2(-tangent[1], tangent[0]),
                ),
                effective_sill_height(opening),
            )
        )

    ordered = sorted(items, key=lambda spec: _spec_key(spec, catalog))
    sizes: dict[int, Vec2] = {}
    heights: dict[int, float] = {}
    candidate_cache: dict[int, list[_Candidate]] = {}
    for index, spec in enumerate(ordered):
        _validate_constraint_targets(document, room, spec)
        entry = _entry_for(catalog, spec.kind_id)
        variant = _variant(entry, spec.variant_id)
        size = (variant.size_m[0], variant.size_m[1])
        sizes[index] = size
        heights[index] = variant.size_m[2]
        min_x, min_z, max_x, max_z = polygon_bounds(insets)
        if (
            size[0] > (max_x - min_x) + 1e-6
            or size[1] > (max_z - min_z) + 1e-6
            or size[0] * size[1] > inset_area * 0.98
        ):
            raise SceneError(
                "item_too_large",
                f"{spec.kind_id} does not fit in room {room.id!r}.",
                "Use a smaller variant or remove another item.",
                {"kindId": spec.kind_id, "roomId": room.id},
            )
        candidates = _candidates_for(document, room, spec, size, rules, insets, entry)
        if not candidates and spec.required:
            raise SceneError(
                "item_too_large",
                f"{spec.kind_id} has no valid wall or floor position in room {room.id!r}.",
                "Use a smaller variant, remove another item, or choose a different wall.",
                {"kindId": spec.kind_id, "roomId": room.id},
            )
        candidate_cache[index] = candidates

    excluded = {spec.existing_item_id for spec in items if spec.existing_item_id is not None}
    initial_obstacles = (
        []
        if options.replace_existing
        else _existing_obstacles(document, room, catalog, exclude_ids=excluded)
    )
    front_zones = _front_zones(initial_obstacles)
    beam = max(1, rules.solver_beam_width)
    states = [_State(obstacles=list(initial_obstacles))]
    evals = 0
    backtracks = 0
    timed_out = False
    eval_budget_exhausted = False
    best_partial: _State | None = None
    best_partial_missing = len(ordered) + 1
    unplaced_reasons: dict[int, tuple[str, str]] = {}

    for index, spec in enumerate(ordered):
        if not states:
            break
        if time.perf_counter() - started >= options.time_budget_ms / 1000.0:
            timed_out = True
            break
        size = sizes[index]
        height = heights[index]
        next_states: list[_State] = []
        for state in states:
            accepted: list[tuple[float, _Candidate, ItemConstraint | None]] = []
            dynamic = _group_candidates(spec, size, catalog, state.placements)
            remaining = max(1, len(ordered) - index)
            per_state = max(30, min(100, options.max_evals // max(1, beam * remaining)))
            static = candidate_cache[index][:per_state]
            for raw_candidate in [*dynamic, *static]:
                if evals >= options.max_evals:
                    eval_budget_exhausted = True
                    break
                if time.perf_counter() - started >= options.time_budget_ms / 1000.0:
                    timed_out = True
                    break
                candidate = _apply_facing(document, room, spec.constraint, raw_candidate, rules)
                evals += 1
                if not _feasible(
                    document,
                    room,
                    spec,
                    candidate,
                    size,
                    height,
                    insets,
                    state,
                    catalog,
                    rules,
                    door_boxes,
                    window_pairs,
                    front_zones,
                ):
                    continue
                box = OBB(candidate.center, size, candidate.yaw)
                score = _candidate_score(
                    document,
                    room,
                    spec,
                    candidate,
                    box,
                    state.placements,
                    state.obstacles,
                    catalog,
                    rules,
                    room_centre,
                    door_boxes,
                )
                accepted.append(
                    (
                        score,
                        candidate,
                        _make_constraint(spec, room, candidate),
                    )
                )
            if not accepted:
                if spec.required and not options.allow_partial:
                    backtracks += 1
                    continue
                unplaced_reasons.setdefault(
                    index,
                    (
                        "no_space",
                        "No collision-free pose was found for this item.",
                    ),
                )
                next_states.append(
                    _State(
                        placements=list(state.placements),
                        obstacles=list(state.obstacles),
                        score=state.score,
                    )
                )
                continue
            accepted.sort(
                key=lambda item: (
                    -item[0],
                    round(item[1].center[0], 4),
                    round(item[1].center[1], 4),
                    round(item[1].yaw, 6),
                )
            )
            for score, candidate, constraint in accepted[: beam * 2]:
                placement = _Placement(
                    spec=spec,
                    index=index,
                    item_id=_stable_item_id(document, room, spec, index),
                    variant_id=spec.variant_id
                    or _entry_for(catalog, spec.kind_id).default_variant_id,
                    size=size,
                    height=height,
                    center=candidate.center,
                    yaw=candidate.yaw,
                    constraint=constraint,
                    candidate=candidate,
                )
                obstacle = _Obstacle(
                    item_id=placement.item_id,
                    kind_id=spec.kind_id,
                    box=OBB(candidate.center, size, candidate.yaw),
                    height=height,
                    allow_overlap=_catalog_allows_overlap(catalog, spec.kind_id),
                    blocker=_is_blocker(catalog.get(spec.kind_id)),
                )
                next_states.append(
                    _State(
                        placements=[*state.placements, placement],
                        obstacles=[*state.obstacles, obstacle],
                        score=state.score + score,
                    )
                )
            del state
        deduped: dict[tuple[tuple[int, int, int], ...], _State] = {}
        for state in next_states:
            key = state.signature()
            existing = deduped.get(key)
            if existing is None or state.score > existing.score:
                deduped[key] = state
        ranked = sorted(
            deduped.values(),
            key=lambda state: _state_sort_key(state, options.seed),
        )
        states = ranked[:beam]
        if timed_out or eval_budget_exhausted:
            break
        best_here = ranked[0] if ranked else None
        if best_here is not None:
            missing = len(ordered) - len(best_here.placements)
            if missing < best_partial_missing:
                best_partial = best_here
                best_partial_missing = missing

    required_indexes = {index for index, spec in enumerate(ordered) if spec.required}

    def is_complete(state: _State) -> bool:
        return required_indexes.issubset({placement.index for placement in state.placements})

    complete = [state for state in states if is_complete(state)]
    chosen: _State | None = None
    if complete:
        chosen = min(complete, key=lambda state: _state_sort_key(state, options.seed))
    elif options.allow_partial:
        chosen = best_partial or (states[0] if states else None)
        if chosen is not None and not chosen.placements:
            chosen = None
    if chosen is None:
        if options.replace_existing and timed_out:
            raise SceneError(
                "no_space",
                f"No items could be placed in room {room_id!r}; existing furniture was kept.",
                "Reduce the item list or relax the constraints.",
                {"roomId": room_id},
            )
        if timed_out and not eval_budget_exhausted:
            raise SceneError(
                "solver_timeout",
                "auto_layout exceeded its search budget before finding a full layout.",
                "Reduce the number of required items, relax constraints, or allowPartial.",
                {"roomId": room_id, "elapsedMs": int((time.perf_counter() - started) * 1000)},
            )
        raise SceneError(
            "no_space",
            f"No collision-free layout fits all required items in room {room_id!r}.",
            "Remove another item, use a smaller variant, or choose a different wall.",
            {"roomId": room_id, "items": [spec.kind_id for spec in ordered]},
        )

    placed_ids = {placement.spec.existing_item_id for placement in chosen.placements}
    unplaced: list[AutoLayoutUnplaced] = []
    placed_indexes = {placement.index for placement in chosen.placements}
    for index, spec in enumerate(ordered):
        if index in placed_indexes:
            continue
        reason, suggest = unplaced_reasons.get(
            index, ("no_space", "No collision-free pose was found.")
        )
        unplaced.append(AutoLayoutUnplaced(kind_id=spec.kind_id, reason=reason, suggest=suggest))
    placements = [
        AutoLayoutPlacement(
            item_id=placement.item_id,
            kind_id=placement.spec.kind_id,
            variant_id=placement.variant_id,
            transform=Transform(
                position=(placement.center[0], 0.0, placement.center[1]),
                rotation_y=placement.yaw,
            ),
            constraint=placement.constraint,
            existing=placement.spec.existing_item_id is not None,
        )
        for placement in chosen.placements
    ]
    removed: list[str] = []
    if options.replace_existing:
        removed = sorted(
            item.id
            for item in document.items
            if item_room_id(document, item) == room.id and item.id not in placed_ids
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return AutoLayoutResult(
        placements=placements,
        unplaced=unplaced,
        removed_item_ids=removed,
        stats=AutoLayoutStats(
            candidates=evals,
            backtracks=backtracks,
            elapsed_ms=elapsed_ms,
            score=round(chosen.score, 6),
            partial=bool(unplaced),
            timed_out=timed_out,
        ),
    )


def _payload_scene(payload: dict[str, Any]) -> SceneDocument:
    return SceneDocument.model_validate(payload["document"])


def _payload_catalog(payload: dict[str, Any]) -> dict[str, CatalogEntry]:
    return {
        entry.kind_id: entry
        for entry in (CatalogEntry.model_validate(raw) for raw in payload["catalog"])
    }


def _payload_rules(payload: dict[str, Any]) -> Rules:
    return Rules.model_validate(payload["rules"])


def solve_layout_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry point: JSON-serialisable in, JSON-serialisable out.

    Errors must not cross the process boundary as exceptions: ``SceneError`` is
    not picklable and would break the pool. Return a tagged envelope instead.
    """
    try:
        specs = [
            SolveSpec(
                kind_id=raw["kindId"],
                variant_id=raw.get("variantId"),
                required=bool(raw.get("required", True)),
                constraint=ItemConstraint.model_validate(raw["constraint"])
                if raw.get("constraint")
                else None,
                existing_item_id=raw.get("existingItemId"),
            )
            for raw in payload["items"]
        ]
        options = SolveOptions(**payload.get("options", {}))
        result = solve_layout(
            _payload_scene(payload),
            _payload_catalog(payload),
            _payload_rules(payload),
            room_id=payload["roomId"],
            items=specs,
            options=options,
        )
        return {"ok": True, "result": result.model_dump(mode="json", by_alias=True)}
    except (SceneError, GeometryError) as error:
        return {
            "ok": False,
            "error": {
                "code": error.code,
                "message": error.message,
                "suggest": error.suggest,
                "details": error.details,
            },
        }
    except Exception as error:  # pragma: no cover - defensive worker boundary
        return {
            "ok": False,
            "error": {
                "code": "internal_error",
                "message": f"auto_layout failed in the worker: {error}",
                "suggest": "Retry once; check the server logs if it persists.",
                "details": {},
            },
        }


__all__ = [
    "SolveOptions",
    "SolveSpec",
    "solve_layout",
    "solve_layout_payload",
]

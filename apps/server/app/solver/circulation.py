"""Main-path connectivity checks for the VLM loop (PRD §6.6, M4-04).

``verify_layout`` deliberately does not include circulation: G3 requires a
fixture that passes the geometric rules but still blocks the door-to-window
route visually. This module rasterises the room, marks furniture footprints as
obstacles and checks that every door can reach the room centre and each window.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from app.contracts.catalog import CatalogEntry
from app.contracts.rules import Rules
from app.contracts.scene import Opening, Room, SceneDocument, Vec2
from app.geometry.collision import (
    item_allows_overlap,
    item_obb,
    item_room_id,
)
from app.geometry.openings import opening_center
from app.geometry.polygon import centroid, inset_polygon

CELL_SIZE = 0.15
TARGET_SEARCH_RADIUS = 0.9
MAX_CELLS = 60_000


@dataclass(frozen=True)
class _Grid:
    min_x: float
    min_z: float
    cols: int
    rows: int
    cell: float
    passable: list[list[bool]]

    def to_cell(self, point: Vec2) -> tuple[int, int]:
        return (
            min(max(int(round((point[0] - self.min_x) / self.cell)), 0), self.cols - 1),
            min(max(int(round((point[1] - self.min_z) / self.cell)), 0), self.rows - 1),
        )

    def center(self, cell: tuple[int, int]) -> Vec2:
        return (self.min_x + cell[0] * self.cell, self.min_z + cell[1] * self.cell)


def _opening_inside_point(room: Room, opening: Opening, depth: float = 0.35) -> Vec2:
    centre = opening_center(room, opening)
    a = room.polygon[opening.edge_index]
    b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
    dx, dz = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return centre
    normal = (-dz / length, dx / length)
    return (centre[0] + normal[0] * depth, centre[1] + normal[1] * depth)


def _build_grid(
    document: SceneDocument,
    room: Room,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> _Grid:
    insets = inset_polygon(room.polygon, rules.room_inset)
    xs = [point[0] for point in insets]
    zs = [point[1] for point in insets]
    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    width = max(max_x - min_x, 0.3)
    depth = max(max_z - min_z, 0.3)
    cell = max(CELL_SIZE, math.sqrt(width * depth / MAX_CELLS))
    cols = max(int(width / cell) + 1, 1)
    rows = max(int(depth / cell) + 1, 1)
    # Main-path checks use the primary passage width.
    inflate = rules.passage_primary / 2.0
    obstacle_shapes = [
        item_obb(item, catalog).to_polygon().buffer(inflate)
        for item in document.items
        if item_room_id(document, item) == room.id and not item_allows_overlap(item, catalog)
    ]
    obstacles = prep(unary_union(obstacle_shapes)) if obstacle_shapes else None
    inside_shape = prep(Polygon(insets))
    passable: list[list[bool]] = []
    for row in range(rows):
        line: list[bool] = []
        z = min_z + row * cell
        for col in range(cols):
            point = Point(min_x + col * cell, z)
            line.append(
                inside_shape.covers(point) and (obstacles is None or not obstacles.covers(point))
            )
        passable.append(line)
    return _Grid(
        min_x=min_x,
        min_z=min_z,
        cols=cols,
        rows=rows,
        cell=cell,
        passable=passable,
    )


def _nearest_free(grid: _Grid, point: Vec2) -> tuple[int, int] | None:
    start = grid.to_cell(point)
    if grid.passable[start[1]][start[0]]:
        return start
    max_radius = int(TARGET_SEARCH_RADIUS / grid.cell)
    candidates: list[tuple[float, int, int]] = []
    for row in range(max(0, start[1] - max_radius), min(grid.rows, start[1] + max_radius + 1)):
        for col in range(max(0, start[0] - max_radius), min(grid.cols, start[0] + max_radius + 1)):
            if not grid.passable[row][col]:
                continue
            centre = grid.center((col, row))
            distance = math.hypot(centre[0] - point[0], centre[1] - point[1])
            if distance <= TARGET_SEARCH_RADIUS:
                candidates.append((distance, col, row))
    if not candidates:
        return None
    _, col, row = min(candidates)
    return (col, row)


def _reachable(grid: _Grid, start: tuple[int, int], target: tuple[int, int]) -> bool:
    if start == target:
        return True
    queue: deque[tuple[int, int]] = deque([start])
    seen = {start}
    while queue:
        col, row = queue.popleft()
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            next_col, next_row = col + dc, row + dr
            if not (0 <= next_col < grid.cols and 0 <= next_row < grid.rows):
                continue
            if (next_col, next_row) in seen or not grid.passable[next_row][next_col]:
                continue
            seen.add((next_col, next_row))
            if (next_col, next_row) == target:
                return True
            queue.append((next_col, next_row))
    return False


def _main_targets(room: Room, document: SceneDocument) -> list[Vec2]:
    targets: list[Vec2] = [centroid(room.polygon)]
    for opening in document.openings:
        if opening.room_id != room.id or opening.type != "window":
            continue
        targets.append(_opening_inside_point(room, opening))
    return targets


def room_path_blocked(
    document: SceneDocument,
    room: Room,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> bool:
    """True when a door cannot reach the room centre or any window."""
    doors = [
        opening
        for opening in document.openings
        if opening.room_id == room.id and opening.type == "door"
    ]
    if not doors:
        return False
    grid = _build_grid(document, room, catalog, rules)
    targets = _main_targets(room, document)
    for door in doors:
        door_cell = _nearest_free(grid, _opening_inside_point(room, door))
        if door_cell is None:
            return True
        for target in targets:
            target_cell = _nearest_free(grid, target)
            if target_cell is None:
                return True
            if not _reachable(grid, door_cell, target_cell):
                return True
    return False


def room_blocking_item_ids(
    document: SceneDocument,
    room: Room,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
    *,
    candidates: list[str] | None = None,
    limit: int = 3,
) -> list[str]:
    """Find deterministic items whose removal restores the main path."""
    if not room_path_blocked(document, room, catalog, rules):
        return []
    ids = candidates
    if ids is None:
        ids = sorted(item.id for item in document.items if item_room_id(document, item) == room.id)
    found: list[str] = []
    for item_id in ids:
        trimmed = document.model_copy(
            update={"items": [item for item in document.items if item.id != item_id]}
        )
        if not room_path_blocked(trimmed, room, catalog, rules):
            found.append(item_id)
            if len(found) >= limit:
                break
    return found


def circulation_issue_confirmed(
    document: SceneDocument,
    room: Room,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
    item_ids: list[str],
) -> bool:
    """Confirm a VLM ``circulation_blocked`` issue with pathfinding.

    The issue is only accepted when the room is blocked *and* removing at least
    one referenced item restores connectivity. Unattributed blockers are
    treated as false positives.
    """
    if not item_ids:
        return False
    return bool(
        room_blocking_item_ids(
            document,
            room,
            catalog,
            rules,
            candidates=sorted(set(item_ids)),
            limit=1,
        )
    )


__all__ = [
    "circulation_issue_confirmed",
    "room_blocking_item_ids",
    "room_path_blocked",
]

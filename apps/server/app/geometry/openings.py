"""Opening validation, placement and re-anchoring (PRD §5.2)."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.contracts.enums import OpeningType
from app.contracts.rules import Rules
from app.contracts.scene import Opening, Room, SceneDocument, Vec2
from app.geometry.errors import GeometryError
from app.geometry.polygon import EPSILON, ensure_ccw, polygon_area
from app.geometry.walls import point_along_edge

DEFAULT_WINDOW_SILL = 0.9
REANCHOR_MAX_DISTANCE = 0.5


@dataclass(frozen=True)
class GeometryWarning:
    code: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _room_height(room: Room, document: SceneDocument) -> float:
    return room.wall_height if room.wall_height is not None else document.defaults.wall_height


def _edge_length(room: Room, edge_index: int) -> float:
    a = room.polygon[edge_index]
    b = room.polygon[(edge_index + 1) % len(room.polygon)]
    return math.hypot(b[0] - a[0], b[1] - a[1])


def opening_center(room: Room, opening: Opening) -> Vec2:
    a = room.polygon[opening.edge_index]
    b = room.polygon[(opening.edge_index + 1) % len(room.polygon)]
    return point_along_edge(a, b, opening.offset)


def opening_is_window(opening: Opening) -> bool:
    return opening.type == OpeningType.WINDOW


def effective_sill_height(opening: Opening) -> float:
    if opening.type != OpeningType.WINDOW:
        return 0.0
    return opening.sill_height if opening.sill_height is not None else DEFAULT_WINDOW_SILL


def validate_openings(document: SceneDocument, rules: Rules | None = None) -> None:
    """Validate every opening against its room edge, wall height and neighbors."""
    rooms = {room.id: room for room in document.rooms}
    seen_ids: set[str] = set()
    by_edge: dict[tuple[str, int], list[Opening]] = defaultdict(list)

    for opening in document.openings:
        if opening.id in seen_ids:
            raise GeometryError(
                "opening_off_wall",
                f"Duplicate opening id {opening.id!r}.",
                "Mint unique opening ids.",
            )
        seen_ids.add(opening.id)
        room = rooms.get(opening.room_id)
        if room is None:
            raise GeometryError(
                "unknown_room",
                f"Opening {opening.id} references missing room {opening.room_id}.",
                "Place the opening on an existing room edge.",
            )
        if opening.edge_index < 0 or opening.edge_index >= len(room.polygon):
            raise GeometryError(
                "opening_off_wall",
                f"Opening {opening.id} edgeIndex {opening.edge_index} is out of range.",
                "Choose an existing wall segment.",
            )
        length = _edge_length(room, opening.edge_index)
        half_width = opening.width / 2.0
        if opening.offset < half_width - EPSILON or opening.offset > length - half_width + EPSILON:
            raise GeometryError(
                "opening_off_wall",
                f"Opening {opening.id} does not fit on its wall segment.",
                "Move the opening or reduce its width.",
                {"edgeLength": length, "offset": opening.offset, "width": opening.width},
            )
        height = _room_height(room, document)
        if opening.height > height + EPSILON:
            raise GeometryError(
                "opening_off_wall",
                f"Opening {opening.id} is taller than the wall.",
                "Reduce the opening height or raise the wall.",
            )
        if opening.type == OpeningType.WINDOW:
            sill = effective_sill_height(opening)
            if sill < -EPSILON or sill + opening.height > height + EPSILON:
                raise GeometryError(
                    "opening_off_wall",
                    f"Window {opening.id} does not fit between sill and lintel.",
                    "Lower the sill or reduce the window height.",
                )
        elif opening.sill_height not in (None, 0):
            raise GeometryError(
                "opening_off_wall",
                f"Door {opening.id} must start at floor level.",
                "Set sillHeight to 0 or omit it.",
            )
        by_edge[(opening.room_id, opening.edge_index)].append(opening)

    for (room_id, edge_index), openings in by_edge.items():
        ordered = sorted(openings, key=lambda item: item.offset)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            previous_end = previous.offset + previous.width / 2.0
            current_start = current.offset - current.width / 2.0
            if current_start < previous_end - EPSILON:
                raise GeometryError(
                    "opening_overlap",
                    f"Openings {previous.id} and {current.id} overlap on {room_id}:e{edge_index}.",
                    "Move one opening along the wall or reduce its width.",
                )


def _project_onto_segment(point: Vec2, a: Vec2, b: Vec2) -> tuple[float, float]:
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length_sq = dx * dx + dz * dz
    if length_sq <= EPSILON:
        return (0.0, math.hypot(point[0] - a[0], point[1] - a[1]))
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length_sq
    t_clamped = max(0.0, min(1.0, t))
    closest = (a[0] + dx * t_clamped, a[1] + dz * t_clamped)
    return (t_clamped, math.hypot(point[0] - closest[0], point[1] - closest[1]))


def reanchor_openings(
    old_room: Room,
    new_room: Room,
    openings: list[Opening],
) -> tuple[list[Opening], list[GeometryWarning]]:
    """Re-anchor a room's openings after its polygon changed (PRD §5.2).

    Each opening keeps its world position: pick the closest new edge and the
    closest point on it. Openings that moved more than 0.5 m or no longer fit
    are removed with an explicit warning.
    """
    warnings: list[GeometryWarning] = []
    kept: list[Opening] = []
    new_polygon = ensure_ccw(new_room.polygon)

    for opening in openings:
        if opening.room_id != old_room.id:
            kept.append(opening)
            continue
        if opening.edge_index >= len(old_room.polygon):
            warnings.append(
                GeometryWarning(
                    "opening_removed",
                    f"Opening {opening.id} referenced a missing edge and was removed.",
                    {"openingId": opening.id},
                )
            )
            continue
        world_point = opening_center(old_room, opening)

        best_edge = -1
        best_distance = math.inf
        best_offset = 0.0
        for edge_index in range(len(new_polygon)):
            a = new_polygon[edge_index]
            b = new_polygon[(edge_index + 1) % len(new_polygon)]
            t, distance = _project_onto_segment(world_point, a, b)
            if distance < best_distance:
                best_distance = distance
                best_edge = edge_index
                best_offset = t * math.hypot(b[0] - a[0], b[1] - a[1])

        if best_edge < 0 or best_distance > REANCHOR_MAX_DISTANCE:
            warnings.append(
                GeometryWarning(
                    "opening_removed",
                    f"Opening {opening.id} could not be re-anchored within 0.5 m and was removed.",
                    {"openingId": opening.id, "distance": best_distance},
                )
            )
            continue

        edge_length = _edge_length(new_room.model_copy(update={"polygon": new_polygon}), best_edge)
        half_width = opening.width / 2.0
        if best_offset < half_width or best_offset > edge_length - half_width:
            warnings.append(
                GeometryWarning(
                    "opening_removed",
                    f"Opening {opening.id} no longer fits on the nearest wall and was removed.",
                    {"openingId": opening.id, "offset": best_offset, "edgeLength": edge_length},
                )
            )
            continue

        updated = opening.model_copy(update={"edge_index": best_edge, "offset": best_offset})
        kept.append(updated)
        warnings.append(
            GeometryWarning(
                "opening_reanchored",
                f"Opening {opening.id} was re-anchored to edge {best_edge}.",
                {
                    "openingId": opening.id,
                    "edgeIndex": best_edge,
                    "offset": best_offset,
                    "distance": best_distance,
                },
            )
        )
    return kept, warnings


def room_area(room: Room) -> float:
    return polygon_area(room.polygon)

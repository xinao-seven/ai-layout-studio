"""Derived wall segments and wall geometry helpers."""

from __future__ import annotations

import math

from app.contracts.ids import wall_segment_id
from app.contracts.scene import Room, SceneDefaults, Vec2, WallSegment
from app.geometry.errors import GeometryError
from app.geometry.polygon import is_ccw, validate_polygon


def wall_inner_normal(a: Vec2, b: Vec2) -> Vec2:
    """Unit normal pointing into the room for a CCW polygon edge."""
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return (0.0, 0.0)
    return (-dz / length, dx / length)


def point_along_edge(a: Vec2, b: Vec2, distance: float) -> Vec2:
    dx = b[0] - a[0]
    dz = b[1] - a[1]
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return a
    ratio = distance / length
    return (a[0] + dx * ratio, a[1] + dz * ratio)


def derive_wall_segments(room: Room, defaults: SceneDefaults) -> list[WallSegment]:
    """Derive one WallSegment per polygon edge (PRD §5.2).

    The polygon must be CCW so the inward normal is deterministic; callers
    normalise the scene document before storing it.
    """
    validate_polygon(room.polygon, min_area=0.0)
    if not is_ccw(room.polygon):
        raise GeometryError(
            "invalid_polygon",
            f"Room {room.id} polygon must be counter-clockwise.",
            "Normalise the polygon winding order (server does this on commit).",
        )

    height = room.wall_height if room.wall_height is not None else defaults.wall_height
    thickness = room.wall_thickness if room.wall_thickness is not None else defaults.wall_thickness
    segments: list[WallSegment] = []
    count = len(room.polygon)
    for index in range(count):
        a = room.polygon[index]
        b = room.polygon[(index + 1) % count]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= 1e-9:
            raise GeometryError(
                "invalid_polygon",
                f"Room {room.id} edge {index} has zero length.",
                "Remove duplicate consecutive vertices.",
            )
        segments.append(
            WallSegment(
                id=wall_segment_id(room.id, index),
                room_id=room.id,
                edge_index=index,
                a=a,
                b=b,
                normal=wall_inner_normal(a, b),
                length=length,
                thickness=thickness,
                height=height,
            )
        )
    return segments

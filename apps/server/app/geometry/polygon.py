"""Polygon helpers built on Shapely."""

from __future__ import annotations

import math
from collections.abc import Sequence

from shapely.geometry import Point, Polygon

from app.contracts.scene import Vec2
from app.geometry.errors import GeometryError

EPSILON = 1e-9


def signed_area(points: Sequence[Vec2]) -> float:
    """Signed area; positive means counter-clockwise in the x/z plane."""
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, z1 = points[index]
        x2, z2 = points[(index + 1) % count]
        total += x1 * z2 - x2 * z1
    return total / 2.0


def polygon_area(points: Sequence[Vec2]) -> float:
    return abs(signed_area(points))


def is_ccw(points: Sequence[Vec2]) -> bool:
    return signed_area(points) > 0


def ensure_ccw(points: Sequence[Vec2]) -> list[Vec2]:
    """Return the polygon in CCW order without dropping vertices."""
    result = list(points)
    return result if is_ccw(result) else list(reversed(result))


def validate_polygon(
    points: Sequence[Vec2],
    *,
    min_area: float = 1.0,
    code_prefix: str = "",
) -> None:
    """Validate a wall-centerline polygon (simple, CCW-safe, large enough).

    ``code_prefix`` is used by callers that need a different refusal code.
    """
    invalid_code = f"{code_prefix}invalid_polygon" if code_prefix else "invalid_polygon"
    if len(points) < 3:
        raise GeometryError(
            invalid_code,
            "A room polygon needs at least three points.",
            "Add more vertices or redraw the room.",
        )
    if len({(round(x, 9), round(z, 9)) for x, z in points}) < 3:
        raise GeometryError(
            invalid_code,
            "A room polygon has duplicate vertices and no area.",
            "Move duplicate vertices apart.",
        )
    try:
        polygon = Polygon(points)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise GeometryError(invalid_code, str(exc)) from exc
    if not polygon.is_valid or polygon.is_empty:
        raise GeometryError(
            invalid_code,
            "The room polygon self-intersects or is invalid.",
            "Remove crossings and keep the outline simple.",
        )
    if polygon.area < min_area - EPSILON:
        raise GeometryError(
            "room_too_small",
            f"Room area {polygon.area:.3f} m² is below the {min_area:.2f} m² minimum.",
            "Enlarge the room footprint.",
        )


def point_in_polygon(point: Vec2, polygon_points: Sequence[Vec2]) -> bool:
    """True when the point lies inside or on the boundary of the polygon."""
    if len(polygon_points) < 3:
        return False
    return Polygon(polygon_points).covers(Point(point))


def centroid(points: Sequence[Vec2]) -> Vec2:
    if len(points) < 3:
        return (0.0, 0.0)
    polygon = Polygon(points)
    if polygon.is_empty:
        return (0.0, 0.0)
    point = polygon.centroid
    return (float(point.x), float(point.y))


def polygon_bounds(points: Sequence[Vec2]) -> tuple[float, float, float, float]:
    """Return (min_x, min_z, max_x, max_z)."""
    xs = [point[0] for point in points]
    zs = [point[1] for point in points]
    return (min(xs), min(zs), max(xs), max(zs))


def inset_polygon(points: Sequence[Vec2], distance: float) -> list[Vec2]:
    """Inner offset of a polygon by ``distance`` meters.

    Raises ``room_too_small`` when the inset area disappears.
    """
    polygon = Polygon(points).buffer(-abs(distance), join_style="mitre")
    if polygon.is_empty or polygon.area <= EPSILON:
        raise GeometryError(
            "room_too_small",
            f"The room leaves no usable area after a {distance:.2f} m wall inset.",
            "Enlarge the room or reduce wall thickness.",
        )
    exterior = list(polygon.exterior.coords)[:-1]
    return [(float(x), float(y)) for x, y in exterior]


def distance(a: Vec2, b: Vec2) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

"""Oriented bounding boxes and the 2D separating-axis test."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon

from app.contracts.scene import Vec2
from app.geometry.polygon import EPSILON


@dataclass(frozen=True)
class OBB:
    """A 2D oriented box in the x/z plane."""

    center: Vec2
    size: Vec2  # width along local x, depth along local z
    yaw: float = 0.0  # radians

    @property
    def half_extents(self) -> Vec2:
        return (self.size[0] / 2.0, self.size[1] / 2.0)

    def axes(self) -> list[Vec2]:
        cos_y = math.cos(self.yaw)
        sin_y = math.sin(self.yaw)
        # Rotation about +Y maps local +X to (cos, -sin) in x/z.
        return [(cos_y, -sin_y), (sin_y, cos_y)]

    def corners(self) -> list[Vec2]:
        (axis_x, axis_z) = self.axes()
        half_w, half_d = self.half_extents
        cx, cz = self.center
        points: list[Vec2] = []
        for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            points.append(
                (
                    cx + axis_x[0] * half_w * sx + axis_z[0] * half_d * sz,
                    cz + axis_x[1] * half_w * sx + axis_z[1] * half_d * sz,
                )
            )
        return points

    def to_polygon(self) -> Polygon:
        return Polygon(self.corners())


def obb_from_transform(
    position: tuple[float, float, float],
    size: Vec2,
    rotation_y: float,
) -> OBB:
    """Build an OBB on the floor plane from an item transform."""
    return OBB(center=(position[0], position[2]), size=size, yaw=rotation_y)


def obb_polygon(obb: OBB) -> Polygon:
    return obb.to_polygon()


def _project(points: list[Vec2], axis: Vec2) -> tuple[float, float]:
    values = [point[0] * axis[0] + point[1] * axis[1] for point in points]
    return (min(values), max(values))


def obb_overlap(a: OBB, b: OBB, *, tolerance: float = EPSILON) -> bool:
    """Separating-axis test; touching edges count as overlapping."""
    corners_a = a.corners()
    corners_b = b.corners()
    for axis in (*a.axes(), *b.axes()):
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        if max_a < min_b - tolerance or max_b < min_a - tolerance:
            return False
    return True

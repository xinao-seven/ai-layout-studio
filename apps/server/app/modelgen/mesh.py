"""Minimal triangle-mesh builder for box-based furniture.

Furniture generated here is built from axis-aligned boxes, which keeps the
generator free of any 3D dependency while still covering cabinets, shelves,
desks, counters and beds. Faces are emitted with outward normals and
counter-clockwise winding so back-face culling works in every renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]

# Face order: +X, -X, +Y, -Y, +Z, -Z. Each entry is (normal, quad as 4 corners).
_FACES: dict[str, Vec3] = {
    "px": (1.0, 0.0, 0.0),
    "nx": (-1.0, 0.0, 0.0),
    "py": (0.0, 1.0, 0.0),
    "ny": (0.0, -1.0, 0.0),
    "pz": (0.0, 0.0, 1.0),
    "nz": (0.0, 0.0, -1.0),
}


@dataclass
class MeshPart:
    """One material group of a model."""

    material: str
    positions: list[Vec3] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3


class ModelBuilder:
    """Accumulates boxes into per-material parts."""

    def __init__(self) -> None:
        self._parts: dict[str, MeshPart] = {}

    def part(self, material: str) -> MeshPart:
        existing = self._parts.get(material)
        if existing is None:
            existing = MeshPart(material=material)
            self._parts[material] = existing
        return existing

    def add_box(
        self,
        material: str,
        low: Vec3,
        high: Vec3,
        *,
        skip_faces: frozenset[str] = frozenset(),
    ) -> None:
        """Add an axis-aligned box spanning ``low``..``high``.

        Degenerate axes (zero thickness) are rejected rather than silently
        producing zero-area triangles.
        """
        x0, y0, z0 = low
        x1, y1, z1 = high
        if x1 <= x0 or y1 <= y0 or z1 <= z0:
            raise ValueError(f"degenerate box {low}..{high}")
        corners: dict[str, list[Vec3]] = {
            "px": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
            "nx": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
            "py": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
            "ny": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
            "pz": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
            "nz": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
        }
        part = self.part(material)
        for name, quad in corners.items():
            if name in skip_faces:
                continue
            normal = _FACES[name]
            base = len(part.positions)
            for corner in quad:
                part.positions.append(corner)
                part.normals.append(normal)
            part.indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))

    def add_slab(self, material: str, low: Vec3, high: Vec3) -> None:
        """Alias that reads better for horizontal panels."""
        self.add_box(material, low, high)

    def build(self) -> list[MeshPart]:
        return [part for part in self._parts.values() if part.positions]

    def recenter(self) -> None:
        """Shift geometry to the catalog frame: x/z centred, minY at 0."""
        low, high = self.bounds()
        dx = -(low[0] + high[0]) / 2.0
        dy = -low[1]
        dz = -(low[2] + high[2]) / 2.0
        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            return
        for part in self._parts.values():
            part.positions = [(x + dx, y + dy, z + dz) for x, y, z in part.positions]

    def bounds(self) -> tuple[Vec3, Vec3]:
        minimum = [float("inf")] * 3
        maximum = [float("-inf")] * 3
        for part in self._parts.values():
            for position in part.positions:
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], position[axis])
                    maximum[axis] = max(maximum[axis], position[axis])
        if any(value == float("inf") for value in minimum):
            raise ValueError("model has no geometry")
        return (
            (minimum[0], minimum[1], minimum[2]),
            (maximum[0], maximum[1], maximum[2]),
        )


def centered_bounds(width: float, depth: float, height: float) -> tuple[Vec3, Vec3]:
    """Bottom-center frame: x/z centred on the origin, y starting at 0."""
    return (
        (-width / 2.0, 0.0, -depth / 2.0),
        (width / 2.0, height, depth / 2.0),
    )

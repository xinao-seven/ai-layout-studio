"""Mesh -> glTF 2.0 binary (GLB) encoding.

Reuses :func:`app.catalog.glb.write_glb` so generated assets and shipped assets
share one container writer, and ``glb_stats`` can validate either kind with the
same reader.
"""

from __future__ import annotations

import struct
from typing import Any

from app.catalog.glb import GlbDocument
from app.modelgen.mesh import MeshPart, Vec3

COMPONENT_FLOAT = 5126
COMPONENT_UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
TRIANGLES = 4

#: Named material presets. Values are linear-ish RGB base colours; keeping the
#: palette tiny avoids texture assets while still letting shelves, carcasses,
#: doors and handles read as different parts.
MATERIALS: dict[str, tuple[float, float, float, float]] = {
    "oak": (0.72, 0.55, 0.37, 1.0),
    "walnut": (0.42, 0.29, 0.20, 1.0),
    "white": (0.90, 0.90, 0.88, 1.0),
    "graphite": (0.20, 0.21, 0.23, 1.0),
    "glass": (0.72, 0.81, 0.86, 1.0),
    "metal": (0.62, 0.63, 0.66, 1.0),
}

DEFAULT_MATERIAL = "oak"


def _padded(binary: bytearray) -> None:
    while len(binary) % 4:
        binary.append(0)


def build_glb_document(
    parts: list[MeshPart],
    *,
    name: str,
    materials: dict[str, tuple[float, float, float, float]] | None = None,
) -> GlbDocument:
    """Encode parts into a single-mesh, multi-primitive GLB document."""
    palette = {**MATERIALS, **(materials or {})}
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    primitives: list[dict[str, Any]] = []
    material_specs: list[dict[str, Any]] = []
    material_index: dict[str, int] = {}

    def add_view(payload: bytes, target: int) -> int:
        _padded(binary)
        offset = len(binary)
        binary.extend(payload)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(payload),
                "target": target,
            }
        )
        return len(buffer_views) - 1

    for part in parts:
        if not part.positions or not part.indices:
            continue
        position_bytes = b"".join(
            struct.pack("<3f", position[0], position[1], position[2]) for position in part.positions
        )
        normal_bytes = b"".join(
            struct.pack("<3f", normal[0], normal[1], normal[2]) for normal in part.normals
        )
        index_bytes = b"".join(struct.pack("<I", index) for index in part.indices)

        minimum, maximum = _bounds(part.positions)
        position_view = add_view(position_bytes, ARRAY_BUFFER)
        normal_view = add_view(normal_bytes, ARRAY_BUFFER)
        index_view = add_view(index_bytes, ELEMENT_ARRAY_BUFFER)

        position_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": position_view,
                "componentType": COMPONENT_FLOAT,
                "count": len(part.positions),
                "type": "VEC3",
                "min": list(minimum),
                "max": list(maximum),
            }
        )
        normal_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": normal_view,
                "componentType": COMPONENT_FLOAT,
                "count": len(part.normals),
                "type": "VEC3",
            }
        )
        index_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": index_view,
                "componentType": COMPONENT_UNSIGNED_INT,
                "count": len(part.indices),
                "type": "SCALAR",
            }
        )

        material = part.material if part.material in palette else DEFAULT_MATERIAL
        if material not in material_index:
            material_index[material] = len(material_specs)
            material_specs.append(
                {
                    "name": material,
                    "doubleSided": False,
                    "pbrMetallicRoughness": {
                        "baseColorFactor": list(palette[material]),
                        "metallicFactor": 0.0 if material != "metal" else 0.7,
                        "roughnessFactor": 0.55 if material == "metal" else 0.75,
                    },
                }
            )
        primitives.append(
            {
                "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor},
                "indices": index_accessor,
                "material": material_index[material],
                "mode": TRIANGLES,
            }
        )

    if not primitives:
        raise ValueError("model has no primitives")

    payload: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "ai-layout-modelgen"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": material_specs,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    return GlbDocument(json=payload, binary=bytes(binary), extra_chunks=())


def _bounds(positions: list[Vec3]) -> tuple[Vec3, Vec3]:
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for position in positions:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], position[axis])
            maximum[axis] = max(maximum[axis], position[axis])
    return (
        (minimum[0], minimum[1], minimum[2]),
        (maximum[0], maximum[1], maximum[2]),
    )

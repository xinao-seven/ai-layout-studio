"""Minimal GLB reader used for catalog asset validation and normalization.

The project does not want a runtime 3D-asset dependency on the server, so this
module implements the small subset of glTF 2.0 needed by the build/QA scripts:
read the JSON + binary chunks, walk the scene node hierarchy with TRS/matrix
transforms, and return the world-space AABB and triangle count of a model.

It is intentionally read-only and standard-library only.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

COMPONENT_FLOAT = 5126
COMPONENT_UNSIGNED_INT = 5125
COMPONENT_UNSIGNED_SHORT = 5123
COMPONENT_UNSIGNED_BYTE = 5121

COMPONENT_FORMATS = {
    COMPONENT_FLOAT: ("f", 4),
    COMPONENT_UNSIGNED_INT: ("I", 4),
    COMPONENT_UNSIGNED_SHORT: ("H", 2),
    COMPONENT_UNSIGNED_BYTE: ("B", 1),
}

TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

Matrix = list[list[float]]


@dataclass(frozen=True)
class GlbDocument:
    json: dict[str, Any]
    binary: bytes
    extra_chunks: tuple[bytes, ...]


@dataclass(frozen=True)
class GlbStats:
    size: tuple[float, float, float]
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    triangles: int


def read_glb(path: Path) -> GlbDocument:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError(f"{path} is not a GLB file")
    magic, version, _length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise ValueError(f"{path} is not a glTF 2.0 binary file")
    offset = 12
    json_payload: dict[str, Any] | None = None
    binary = b""
    extra: list[bytes] = []
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        payload = data[offset : offset + chunk_length]
        offset += chunk_length
        if chunk_type == JSON_CHUNK and json_payload is None:
            json_payload = json.loads(payload.decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            binary = payload
        else:
            extra.append(payload)
    if json_payload is None:
        raise ValueError(f"{path} has no JSON chunk")
    return GlbDocument(json=json_payload, binary=binary, extra_chunks=tuple(extra))


def write_glb(path: Path, document: GlbDocument) -> None:
    json_bytes = json.dumps(
        document.json, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    chunks = [(JSON_CHUNK, json_bytes)]
    if document.binary:
        binary = document.binary
        binary += b"\x00" * ((4 - len(binary) % 4) % 4)
        chunks.append((BIN_CHUNK, binary))
    for payload in document.extra_chunks:
        chunks.append((0, payload))
    total = 12 + sum(8 + len(payload) for _, payload in chunks)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    for chunk_type, payload in chunks:
        out += struct.pack("<II", len(payload), chunk_type)
        out += payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def _identity() -> Matrix:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    return [
        [sum(a[row][k] * b[k][column] for k in range(4)) for column in range(4)] for row in range(4)
    ]


def _from_column_major(values: list[float]) -> Matrix:
    return [[float(values[column * 4 + row]) for column in range(4)] for row in range(4)]


def _trs_matrix(node: dict[str, Any]) -> Matrix:
    if "matrix" in node:
        return _from_column_major(node["matrix"])
    tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
    qx, qy, qz, qw = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])

    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    rotation = [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy), 0.0],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx), 0.0],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    scale = _identity()
    scale[0][0], scale[1][1], scale[2][2] = float(sx), float(sy), float(sz)
    translation = _identity()
    translation[0][3], translation[1][3], translation[2][3] = float(tx), float(ty), float(tz)
    return _multiply(_multiply(translation, rotation), scale)


def _transform_point(
    matrix: Matrix, point: tuple[float, float, float]
) -> tuple[float, float, float]:
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _read_accessor_values(document: GlbDocument, accessor_index: int) -> list[tuple[float, ...]]:
    accessor = document.json["accessors"][accessor_index]
    if "sparse" in accessor:
        raise ValueError("sparse accessors are not supported")
    view = document.json["bufferViews"][accessor["bufferView"]]
    component_type = accessor["componentType"]
    if component_type not in COMPONENT_FORMATS:
        raise ValueError(f"unsupported component type {component_type}")
    fmt, size = COMPONENT_FORMATS[component_type]
    components = TYPE_COMPONENTS[accessor["type"]]
    count = accessor["count"]
    stride = view.get("byteStride", components * size)
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    values: list[tuple[float, ...]] = []
    for index in range(count):
        start = offset + index * stride
        values.append(struct.unpack_from(f"<{components}{fmt}", document.binary, start))
    return values


def _accessor_bounds(
    document: GlbDocument, accessor_index: int
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    accessor = document.json["accessors"][accessor_index]
    if "min" in accessor and "max" in accessor:
        minimum_values = [float(value) for value in accessor["min"]]
        maximum_values = [float(value) for value in accessor["max"]]
        return (
            (minimum_values[0], minimum_values[1], minimum_values[2]),
            (maximum_values[0], maximum_values[1], maximum_values[2]),
        )
    values = _read_accessor_values(document, accessor_index)
    if not values:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    minimum = tuple(min(value[i] for value in values) for i in range(3))
    maximum = tuple(max(value[i] for value in values) for i in range(3))
    return (
        (float(minimum[0]), float(minimum[1]), float(minimum[2])),
        (float(maximum[0]), float(maximum[1]), float(maximum[2])),
    )


def _mesh_triangles(document: GlbDocument, mesh_index: int) -> int:
    mesh = document.json["meshes"][mesh_index]
    triangles = 0
    for primitive in mesh.get("primitives", []):
        mode = primitive.get("mode", 4)
        if mode != 4:
            continue
        if "indices" in primitive:
            accessor = document.json["accessors"][primitive["indices"]]
            triangles += int(accessor["count"] // 3)
        else:
            position_index = primitive.get("attributes", {}).get("POSITION")
            if position_index is not None:
                accessor = document.json["accessors"][position_index]
                triangles += int(accessor["count"] // 3)
    return triangles


def _scene_roots(document: GlbDocument) -> list[int]:
    scenes = document.json.get("scenes")
    if scenes:
        scene_index = document.json.get("scene", 0)
        return list(scenes[scene_index].get("nodes", []))
    return list(range(len(document.json.get("nodes", []))))


def glb_stats(path: Path) -> GlbStats:
    document = read_glb(path)
    nodes = document.json.get("nodes", [])
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    triangles = 0

    def visit(node_index: int, parent: Matrix) -> None:
        nonlocal triangles
        node = nodes[node_index]
        world = _multiply(parent, _trs_matrix(node))
        if "mesh" in node:
            triangles += _mesh_triangles(document, node["mesh"])
            mesh = document.json["meshes"][node["mesh"]]
            for primitive in mesh.get("primitives", []):
                position_index = primitive.get("attributes", {}).get("POSITION")
                if position_index is None:
                    continue
                local_min, local_max = _accessor_bounds(document, position_index)
                for x in (local_min[0], local_max[0]):
                    for y in (local_min[1], local_max[1]):
                        for z in (local_min[2], local_max[2]):
                            point = _transform_point(world, (x, y, z))
                            for axis in range(3):
                                minimum[axis] = min(minimum[axis], point[axis])
                                maximum[axis] = max(maximum[axis], point[axis])
        for child in node.get("children", []):
            visit(child, world)

    for root in _scene_roots(document):
        visit(root, _identity())
    if any(math.isinf(value) for value in minimum):
        raise ValueError(f"{path} has no renderable mesh")
    size = tuple(maximum[axis] - minimum[axis] for axis in range(3))
    return GlbStats(
        size=(size[0], size[1], size[2]),
        minimum=(minimum[0], minimum[1], minimum[2]),
        maximum=(maximum[0], maximum[1], maximum[2]),
        triangles=triangles,
    )

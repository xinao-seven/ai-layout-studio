"""Uniform / per-axis rescaling of an existing GLB (the ``scale`` family).

Scaling node transforms instead of rewriting vertex buffers keeps the source
geometry untouched and the result exactly linear in the requested factors.
``glb_stats`` already walks node TRS/matrix transforms, so the declared size of
the scaled asset is measured, not assumed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from app.catalog.glb import GlbDocument, read_glb

Vec3Factors = tuple[float, float, float]


def scale_document(path: Path, factors: Vec3Factors) -> GlbDocument:
    document = read_glb(path)
    payload = copy.deepcopy(document.json)
    sx, sy, sz = factors
    nodes = payload.get("nodes", [])
    # Scale only scene roots. Scaled assets here are normalised with a parent
    # node holding the unit conversion, so scaling every node would apply the
    # factor once per ancestor level.
    roots = _scene_roots(payload, len(nodes))

    for index_node in roots:
        node = nodes[index_node]
        if not isinstance(node, dict):
            continue
        if "matrix" in node and isinstance(node["matrix"], list):
            # Column-major 4x4: element index = column * 4 + row, so
            # pre-multiplying by a diagonal scale multiplies each row.
            matrix = list(node["matrix"])
            for element in range(len(matrix)):
                row = element % 4
                if row == 0:
                    matrix[element] = float(matrix[element]) * sx
                elif row == 1:
                    matrix[element] = float(matrix[element]) * sy
                elif row == 2:
                    matrix[element] = float(matrix[element]) * sz
            node["matrix"] = matrix
            continue
        translation = node.get("translation", [0.0, 0.0, 0.0])
        node["translation"] = [
            float(translation[0]) * sx,
            float(translation[1]) * sy,
            float(translation[2]) * sz,
        ]
        scale = node.get("scale", [1.0, 1.0, 1.0])
        node["scale"] = [
            float(scale[0]) * sx,
            float(scale[1]) * sy,
            float(scale[2]) * sz,
        ]

    payload["asset"] = {
        **payload.get("asset", {}),
        "generator": "ai-layout-modelgen (scale)",
    }
    return GlbDocument(
        json=payload,
        binary=document.binary,
        extra_chunks=document.extra_chunks,
    )


def _scene_roots(payload: dict[str, Any], node_count: int) -> list[int]:
    """Root node indices: declared scene roots, else nodes nobody parents."""
    scenes = payload.get("scenes")
    if isinstance(scenes, list) and scenes:
        scene_index = int(payload.get("scene", 0) or 0)
        if 0 <= scene_index < len(scenes):
            declared = scenes[scene_index].get("nodes")
            if isinstance(declared, list) and declared:
                return [int(index) for index in declared if int(index) < node_count]
    children: set[int] = set()
    for node in payload.get("nodes", []):
        if isinstance(node, dict):
            for child in node.get("children", []) or []:
                children.add(int(child))
    return [index for index in range(node_count) if index not in children]
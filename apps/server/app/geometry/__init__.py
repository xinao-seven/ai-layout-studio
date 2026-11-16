"""Geometry kernel (Shapely is authoritative; PRD D16)."""

from app.geometry.errors import GeometryError
from app.geometry.obb import OBB, obb_from_transform, obb_overlap, obb_polygon
from app.geometry.openings import (
    opening_center,
    reanchor_openings,
    validate_openings,
)
from app.geometry.polygon import (
    centroid,
    ensure_ccw,
    inset_polygon,
    point_in_polygon,
    polygon_area,
    polygon_bounds,
    signed_area,
    validate_polygon,
)
from app.geometry.walls import derive_wall_segments, wall_inner_normal

__all__ = [
    "OBB",
    "GeometryError",
    "centroid",
    "derive_wall_segments",
    "ensure_ccw",
    "inset_polygon",
    "obb_from_transform",
    "obb_overlap",
    "obb_polygon",
    "opening_center",
    "point_in_polygon",
    "polygon_area",
    "polygon_bounds",
    "reanchor_openings",
    "signed_area",
    "validate_openings",
    "validate_polygon",
    "wall_inner_normal",
]

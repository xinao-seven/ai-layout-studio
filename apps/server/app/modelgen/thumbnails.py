"""Thumbnail rendering for generated assets.

The catalog contract requires a 256x256 PNG per variant. Rather than adding a
headless 3D renderer, generated models are drawn as a flat orthographic
silhouette with painter's-order fill — deterministic, dependency-light (Pillow
is already used by the catalog validator) and enough to read the proportions in
a catalog chip.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.modelgen.glb_builder import MATERIALS
from app.modelgen.mesh import MeshPart

THUMBNAIL_SIZE = 256
SUPERSAMPLE = 2
PADDING_RATIO = 0.08
FALLBACK_COLOUR = (183, 140, 94, 255)


def _colour(material: str) -> tuple[int, int, int, int]:
    base = MATERIALS.get(material, MATERIALS["oak"])
    return (
        int(base[0] * 255),
        int(base[1] * 255),
        int(base[2] * 255),
        255,
    )


def render_thumbnail(
    parts: list[MeshPart] | None,
    path: Path,
    *,
    size_m: tuple[float, float, float] | None = None,
) -> None:
    """Draw a front-view silhouette and write a ``size``x``size`` PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = THUMBNAIL_SIZE * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (247, 245, 242, 255))
    draw = ImageDraw.Draw(image)

    if not parts:
        _draw_fallback(draw, size_m, canvas)
        image.resize((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS).convert(
            "RGB"
        ).save(path)
        return

    xs = [position[0] for part in parts for position in part.positions]
    ys = [position[1] for part in parts for position in part.positions]
    if not xs or not ys:
        _draw_fallback(draw, size_m, canvas)
        image.resize((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS).convert(
            "RGB"
        ).save(path)
        return

    minimum_x, maximum_x = min(xs), max(xs)
    minimum_y, maximum_y = min(ys), max(ys)
    span_x = max(maximum_x - minimum_x, 1e-6)
    span_y = max(maximum_y - minimum_y, 1e-6)
    usable = canvas * (1.0 - 2 * PADDING_RATIO)
    scale = min(usable / span_x, usable / span_y)
    offset_x = (canvas - span_x * scale) / 2
    offset_y = (canvas - span_y * scale) / 2

    def project(position: tuple[float, float, float]) -> tuple[float, float]:
        x = offset_x + (position[0] - minimum_x) * scale
        # Screen Y grows downwards; model Y grows upwards.
        y = canvas - offset_y - (position[1] - minimum_y) * scale
        return (x, y)

    triangles: list[tuple[float, str, list[tuple[float, float]]]] = []
    for part in parts:
        colour = part.material
        for offset in range(0, len(part.indices), 3):
            corners = [part.positions[part.indices[offset + i]] for i in range(3)]
            depth = sum(corner[2] for corner in corners) / 3.0
            triangles.append((depth, colour, [project(corner) for corner in corners]))

    # Painter's algorithm: draw the farthest (lowest z) triangles first.
    triangles.sort(key=lambda item: item[0])
    for _, material, polygon in triangles:
        draw.polygon(polygon, fill=_colour(material))

    image.resize((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS).convert("RGB").save(
        path
    )


def _draw_fallback(
    draw: ImageDraw.ImageDraw,
    size_m: tuple[float, float, float] | None,
    canvas: int,
) -> None:
    """Draw a proportional block for families without renderable parts."""
    if size_m is None or size_m[0] <= 0 or size_m[2] <= 0:
        width_ratio, height_ratio = 0.6, 0.6
    else:
        aspect = size_m[0] / size_m[2]
        width_ratio = min(0.8, max(0.3, 0.5 * aspect))
        height_ratio = min(0.8, max(0.3, 0.5 / aspect))
    half_w = canvas * width_ratio / 2
    half_h = canvas * height_ratio / 2
    centre = canvas / 2
    draw.rectangle(
        (centre - half_w, centre - half_h, centre + half_w, centre + half_h),
        fill=FALLBACK_COLOUR,
        outline=(120, 96, 70, 255),
        width=max(2, canvas // 128),
    )
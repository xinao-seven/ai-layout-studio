"""Box-based parametric families.

Each builder returns a :class:`FamilyBuild`: an encoded GLB document, the size
the geometry is expected to measure, and the catalog metadata that makes the
result a first-class catalog entry. Builders are pure and deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.catalog.glb import GlbDocument
from app.contracts.catalog import CatalogEntry, CatalogRules
from app.contracts.enums import CatalogCategory
from app.contracts.scene import Clearance
from app.modelgen.glb_builder import build_glb_document
from app.modelgen.mesh import MeshPart, ModelBuilder
from app.modelgen.specs import (
    BookshelfSpec,
    CounterRunSpec,
    DeskLSpec,
    PrimitivesSpec,
    ScaleSpec,
    SpecBase,
    TvStandSpec,
    WardrobeSpec,
)

PANEL = 0.018
BACK_PANEL = 0.008
TOE_KICK_HEIGHT = 0.10
TOE_KICK_INSET = 0.05
#: Depth reserved in front of a door/drawer front for its handle. Doors are
#: recessed by this amount so the handle — not the panel — ends flush with the
#: declared front plane, keeping the measured depth equal to the spec.
HANDLE_DEPTH = 0.022
SIZE_TOLERANCE = 0.003

#: Composed objects are a long-tail escape hatch, so they get a tighter size
#: envelope than the curated family defaults: big enough for real furniture,
#: small enough that a bad spec cannot ship a 20 m object.
MAX_PRIMITIVE_EXTENT = 3.5
MIN_PRIMITIVE_EXTENT = 0.02

ROTATIONS: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)

#: Rescaling is only meaningful for boxy joinery; decor, lighting and rugs do
#: not keep their proportions or their placement rules when stretched.
SCALABLE_CATEGORIES: frozenset[CatalogCategory] = frozenset(
    {CatalogCategory.STORAGE, CatalogCategory.TABLE, CatalogCategory.BED}
)


@dataclass(frozen=True)
class SourceAsset:
    """A resolvable existing model plus its catalog entry (for inheritance)."""

    path: Path
    entry: CatalogEntry | None = None


@dataclass(frozen=True)
class FamilyBuild:
    document: GlbDocument
    expected_size_m: tuple[float, float, float] | None
    category: CatalogCategory
    tags: tuple[str, ...]
    must_against_wall: bool
    clearance: Clearance
    allowed_rotations: tuple[float, ...]
    render_parts: list[MeshPart] | None

    def rules(self) -> CatalogRules:
        return CatalogRules(
            must_against_wall=self.must_against_wall,
            group_with=[],
            anchor_priority=0,
            allow_overlap=False,
            room_types=[],
        )


ModelPathResolver = Callable[[str], SourceAsset | None]


def _finish(
    builder: ModelBuilder,
    *,
    name: str,
    expected_size_m: tuple[float, float, float],
    category: CatalogCategory,
    tags: tuple[str, ...],
    must_against_wall: bool,
    clearance: Clearance,
) -> FamilyBuild:
    builder.recenter()
    low, high = builder.bounds()
    measured = (high[0] - low[0], high[2] - low[2], high[1] - low[1])
    for axis, (declared, actual) in enumerate(zip(expected_size_m, measured, strict=True)):
        if abs(declared - actual) > SIZE_TOLERANCE:
            raise ValueError(
                f"{name}: declared size[{axis}]={declared:.3f} but geometry measures {actual:.3f}"
            )
    parts = builder.build()
    document = build_glb_document(parts, name=name)
    return FamilyBuild(
        document=document,
        expected_size_m=measured,
        category=category,
        tags=tags,
        must_against_wall=must_against_wall,
        clearance=clearance,
        allowed_rotations=ROTATIONS,
        render_parts=parts,
    )


# --------------------------------------------------------------------------
# bookshelf
# --------------------------------------------------------------------------


def _bookshelf(spec: BookshelfSpec) -> FamilyBuild:
    width, depth, height = spec.size_m()
    thickness = PANEL
    builder = ModelBuilder()
    material = spec.material

    builder.add_box(
        material, (-width / 2, 0, -depth / 2), (-width / 2 + thickness, height, depth / 2)
    )
    builder.add_box(
        material, (width / 2 - thickness, 0, -depth / 2), (width / 2, height, depth / 2)
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, height - thickness, -depth / 2),
        (width / 2 - thickness, height, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, 0, -depth / 2),
        (width / 2 - thickness, thickness, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, thickness, -depth / 2),
        (width / 2 - thickness, height - thickness, -depth / 2 + BACK_PANEL),
    )

    has_doors = spec.door_count > 0
    shelf_front = depth / 2 - thickness if has_doors else depth / 2
    inner_height = height - 2 * thickness
    for index in range(1, spec.shelves + 1):
        centre = thickness + inner_height * index / (spec.shelves + 1)
        builder.add_box(
            material,
            (-width / 2 + thickness, centre - thickness / 2, -depth / 2 + BACK_PANEL),
            (width / 2 - thickness, centre + thickness / 2, shelf_front),
        )

    if has_doors:
        _add_doors(
            builder,
            width=width - 2 * thickness,
            height=height - 2 * thickness,
            front_z=depth / 2,
            thickness=thickness,
            door_count=spec.door_count,
            material=material,
            glass=spec.glass_doors,
            offset_x=-width / 2 + thickness,
            offset_y=thickness,
        )

    return _finish(
        builder,
        name=spec.display_name(),
        expected_size_m=spec.size_m(),
        category=CatalogCategory.STORAGE,
        tags=("bookshelf", "书柜", "书架", "custom"),
        must_against_wall=True,
        clearance=Clearance(front=0.5, sides=0.02, back=0.02),
    )


# --------------------------------------------------------------------------
# wardrobe
# --------------------------------------------------------------------------


def _wardrobe(spec: WardrobeSpec) -> FamilyBuild:
    width, depth, height = spec.size_m()
    thickness = PANEL
    builder = ModelBuilder()
    material = spec.material

    builder.add_box(
        material, (-width / 2, 0, -depth / 2), (-width / 2 + thickness, height, depth / 2)
    )
    builder.add_box(
        material, (width / 2 - thickness, 0, -depth / 2), (width / 2, height, depth / 2)
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, height - thickness, -depth / 2),
        (width / 2 - thickness, height, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, 0, -depth / 2),
        (width / 2 - thickness, thickness, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, thickness, -depth / 2),
        (width / 2 - thickness, height - thickness, -depth / 2 + BACK_PANEL),
    )

    if spec.with_rail:
        rail_y = height - 0.12
        builder.add_box(
            "metal",
            (-width / 2 + thickness, rail_y, -depth / 4 - 0.01),
            (width / 2 - thickness, rail_y + 0.025, -depth / 4 + 0.01),
        )
    # A single fixed shelf keeps the carcass readable on tall units.
    shelf_y = height * 0.5
    builder.add_box(
        material,
        (-width / 2 + thickness, shelf_y - thickness / 2, -depth / 2 + BACK_PANEL),
        (width / 2 - thickness, shelf_y + thickness / 2, depth / 2 - thickness),
    )

    sliding = spec.door_type == "sliding"
    _add_doors(
        builder,
        width=width - 2 * thickness,
        height=height - 2 * thickness,
        front_z=depth / 2,
        thickness=thickness if not sliding else thickness * 1.2,
        door_count=spec.door_count,
        material=material,
        glass=False,
        offset_x=-width / 2 + thickness,
        offset_y=thickness,
        overlapping=sliding,
    )

    return _finish(
        builder,
        name=spec.display_name(),
        expected_size_m=spec.size_m(),
        category=CatalogCategory.STORAGE,
        tags=("wardrobe", "衣柜", "custom"),
        must_against_wall=True,
        clearance=Clearance(front=0.6, sides=0.02, back=0.02),
    )


# --------------------------------------------------------------------------
# tv stand
# --------------------------------------------------------------------------


def _tv_stand(spec: TvStandSpec) -> FamilyBuild:
    width, depth, height = spec.size_m()
    thickness = PANEL
    builder = ModelBuilder()
    material = spec.material

    builder.add_box(
        material, (-width / 2, 0, -depth / 2), (-width / 2 + thickness, height, depth / 2)
    )
    builder.add_box(
        material, (width / 2 - thickness, 0, -depth / 2), (width / 2, height, depth / 2)
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, height - thickness, -depth / 2),
        (width / 2 - thickness, height, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, 0, -depth / 2),
        (width / 2 - thickness, thickness, depth / 2),
    )
    builder.add_box(
        material,
        (-width / 2 + thickness, thickness, -depth / 2),
        (width / 2 - thickness, height - thickness, -depth / 2 + BACK_PANEL),
    )

    inner_width = width - 2 * thickness
    if spec.drawers > 0 and spec.open_shelf:
        split = -width / 2 + thickness + inner_width * 0.62
        builder.add_box(
            material,
            (split, thickness, -depth / 2 + BACK_PANEL),
            (split + thickness, height - thickness, depth / 2),
        )
        drawer_left, drawer_width = -width / 2 + thickness, split - (-width / 2 + thickness)
        shelf_center = thickness + (height - 2 * thickness) / 2
        builder.add_box(
            material,
            (split + thickness, shelf_center - thickness / 2, -depth / 2),
            (width / 2 - thickness, shelf_center + thickness / 2, depth / 2),
        )
    else:
        drawer_left, drawer_width = -width / 2 + thickness, inner_width

    if spec.drawers > 0:
        drawer_height = (height - 2 * thickness) / spec.drawers
        panel_front = depth / 2 - HANDLE_DEPTH
        for index in range(spec.drawers):
            low_y = thickness + index * drawer_height + 0.004
            high_y = thickness + (index + 1) * drawer_height - 0.004
            builder.add_box(
                material,
                (drawer_left + 0.004, low_y, panel_front - thickness),
                (drawer_left + drawer_width - 0.004, high_y, panel_front),
            )
            handle_y = (low_y + high_y) / 2
            builder.add_box(
                "metal",
                (drawer_left + drawer_width / 2 - 0.08, handle_y - 0.012, panel_front),
                (drawer_left + drawer_width / 2 + 0.08, handle_y + 0.012, depth / 2),
            )

    return _finish(
        builder,
        name=spec.display_name(),
        expected_size_m=spec.size_m(),
        category=CatalogCategory.STORAGE,
        tags=("tv_stand", "电视柜", "custom"),
        must_against_wall=True,
        clearance=Clearance(front=0.45, sides=0.02, back=0.02),
    )


# --------------------------------------------------------------------------
# L-shaped desk
# --------------------------------------------------------------------------


def _desk_l(spec: DeskLSpec) -> FamilyBuild:
    width, main_depth, height = spec.width_m, spec.depth_m, spec.height_m
    return_width, return_length = spec.return_width_m, spec.return_length_m
    top = 0.03
    leg = 0.04
    builder = ModelBuilder()
    material = spec.material

    # Main top: z from -main_depth/2..main_depth/2, return extends towards -Z.
    builder.add_slab(
        material,
        (-width / 2, height - top, -main_depth / 2),
        (width / 2, height, main_depth / 2),
    )
    builder.add_slab(
        material,
        (-width / 2, height - top, -main_depth / 2 - return_length),
        (-width / 2 + return_width, height, -main_depth / 2),
    )

    for x in (-width / 2 + leg / 2, width / 2 - leg / 2):
        builder.add_box(
            material,
            (x - leg / 2, 0, main_depth / 2 - leg),
            (x + leg / 2, height - top, main_depth / 2),
        )
        builder.add_box(
            material,
            (x - leg / 2, 0, -main_depth / 2),
            (x + leg / 2, height - top, -main_depth / 2 + leg),
        )
    return_leg_x = -width / 2 + leg / 2
    builder.add_box(
        material,
        (return_leg_x - leg / 2, 0, -main_depth / 2 - return_length),
        (return_leg_x + leg / 2, height - top, -main_depth / 2 - return_length + leg),
    )

    if spec.drawer_count > 0:
        unit_height = height - top
        drawer_height = unit_height / spec.drawer_count
        unit_x0 = width / 2 - 0.42
        for index in range(spec.drawer_count):
            low_y = index * drawer_height + 0.004
            high_y = (index + 1) * drawer_height - 0.004
            builder.add_box(
                material,
                (unit_x0, low_y, -main_depth / 2 + 0.02),
                (width / 2 - 0.02, high_y, main_depth / 2 - 0.02),
            )
            handle_y = (low_y + high_y) / 2
            builder.add_box(
                "metal",
                (unit_x0 + 0.06, handle_y - 0.01, main_depth / 2 - 0.02),
                (unit_x0 + 0.20, handle_y + 0.01, main_depth / 2),
            )

    return _finish(
        builder,
        name=spec.display_name(),
        expected_size_m=spec.size_m(),
        category=CatalogCategory.TABLE,
        tags=("desk", "书桌", "L型", "custom"),
        must_against_wall=False,
        clearance=Clearance(front=0.75, sides=0.1, back=0.05),
    )


# --------------------------------------------------------------------------
# kitchen counter run
# --------------------------------------------------------------------------


def _counter_run(spec: CounterRunSpec) -> FamilyBuild:
    length, depth, height = spec.size_m()
    builder = ModelBuilder()
    material = spec.material
    body_height = height - 0.04

    # Toe kick is recessed at the front so the run reads as built-in joinery.
    builder.add_box(
        "graphite",
        (-length / 2, 0, -depth / 2),
        (length / 2, TOE_KICK_HEIGHT, depth / 2 - TOE_KICK_INSET),
    )
    builder.add_box(
        material,
        (-length / 2, TOE_KICK_HEIGHT, -depth / 2),
        (length / 2, body_height, depth / 2 - 0.02),
    )
    builder.add_slab(
        material, (-length / 2, body_height, -depth / 2), (length / 2, height, depth / 2)
    )

    segment_width = length / spec.segments
    for index in range(1, spec.segments):
        x = -length / 2 + index * segment_width
        builder.add_box(
            material,
            (x - 0.01, TOE_KICK_HEIGHT, depth / 2 - 0.04),
            (x + 0.01, body_height, depth / 2 - 0.02),
        )
    # Door handles, one per segment, ending flush with the front plane.
    for index in range(spec.segments):
        centre = -length / 2 + (index + 0.5) * segment_width
        builder.add_box(
            "metal",
            (centre - 0.09, body_height - 0.06, depth / 2 - 0.02),
            (centre + 0.09, body_height - 0.04, depth / 2),
        )

    if spec.sink:
        builder.add_slab(
            "metal",
            (-length / 2 + 0.25, height - 0.012, -depth / 2 + 0.12),
            (-length / 2 + 0.25 + 0.55, height, depth / 2 - 0.12),
        )
    if spec.cooktop:
        builder.add_slab(
            "graphite",
            (length / 2 - 0.85, height - 0.012, -depth / 2 + 0.1),
            (length / 2 - 0.25, height, depth / 2 - 0.1),
        )

    return _finish(
        builder,
        name=spec.display_name(),
        expected_size_m=spec.size_m(),
        category=CatalogCategory.STORAGE,
        tags=("counter", "橱柜", "厨房", "custom"),
        must_against_wall=True,
        clearance=Clearance(front=0.9, sides=0.02, back=0.02),
    )


# --------------------------------------------------------------------------
# shared door helper
# --------------------------------------------------------------------------


def _add_doors(
    builder: ModelBuilder,
    *,
    width: float,
    height: float,
    front_z: float,
    thickness: float,
    door_count: int,
    material: str,
    glass: bool,
    offset_x: float,
    offset_y: float,
    overlapping: bool = False,
) -> None:
    gap = 0.004
    door_width = (width - gap * (door_count - 1)) / door_count
    # The door panel is recessed by HANDLE_DEPTH so the handle lands flush with
    # the declared front plane instead of overshooting the asset depth.
    door_front = front_z - HANDLE_DEPTH
    for index in range(door_count):
        x0 = offset_x + index * (door_width + gap)
        x1 = x0 + door_width
        z1 = door_front
        z0 = z1 - thickness
        # Sliding doors sit on two tracks so adjacent leaves overlap in depth.
        if overlapping and index % 2 == 1:
            z0 -= thickness * 0.9
        if glass:
            # Narrow doors cannot carry a 5 cm frame: clamp it so the inner
            # glass panel always stays a valid box.
            frame = min(0.05, door_width / 4.0, height / 4.0)
            panel = "glass"
            builder.add_box(material, (x0, offset_y, z0), (x1, offset_y + frame, z1))
            builder.add_box(
                material, (x0, offset_y + height - frame, z0), (x1, offset_y + height, z1)
            )
            builder.add_box(
                material, (x0, offset_y + frame, z0), (x0 + frame, offset_y + height - frame, z1)
            )
            builder.add_box(
                material, (x1 - frame, offset_y + frame, z0), (x1, offset_y + height - frame, z1)
            )
            builder.add_box(
                panel,
                (x0 + frame, offset_y + frame, z0 + thickness * 0.35),
                (x1 - frame, offset_y + height - frame, z1 - thickness * 0.35),
            )
        else:
            builder.add_box(material, (x0, offset_y, z0), (x1, offset_y + height, z1))
        handle_x = x1 - 0.06 if index % 2 == 0 else x0 + 0.06
        builder.add_box(
            "metal",
            (handle_x - 0.008, offset_y + height * 0.45, z1),
            (handle_x + 0.008, offset_y + height * 0.45 + 0.18, front_z),
        )


# --------------------------------------------------------------------------
# scale family
# --------------------------------------------------------------------------


def _scale(spec: SpecBase, resolve_model_path: ModelPathResolver | None) -> FamilyBuild:
    from app.modelgen.scaling import scale_document

    if not isinstance(spec, ScaleSpec):
        raise ValueError("scale family received a non-scale spec")
    if resolve_model_path is None:
        raise ValueError("scale family needs a model path resolver")
    source = resolve_model_path(spec.source_kind_id)
    if source is None or not source.path.is_file():
        raise LookupError(f"unknown source model {spec.source_kind_id!r}")
    entry = source.entry
    if entry is not None and entry.category not in SCALABLE_CATEGORIES:
        raise ValueError(
            f"{spec.source_kind_id!r} ({entry.category.value}) cannot be proportionally rescaled"
        )
    document = scale_document(
        source.path,
        (spec.factor_x, spec.factor_y, spec.factor_z),
    )
    # A rescaled item keeps the meaning of the kind it came from: category,
    # wall requirement, clearance and rotation set all carry over.
    rules = entry.rules if entry is not None else None
    return FamilyBuild(
        document=document,
        expected_size_m=None,
        category=entry.category if entry is not None else CatalogCategory.OTHER,
        tags=("scaled", "缩放", "custom", spec.source_kind_id),
        must_against_wall=bool(rules and rules.must_against_wall),
        clearance=entry.clearance
        if entry is not None
        else Clearance(front=0.4, sides=0.05, back=0.02),
        allowed_rotations=(tuple(entry.allowed_rotations) if entry is not None else ROTATIONS),
        render_parts=None,
    )


# --------------------------------------------------------------------------
# composed primitives
# --------------------------------------------------------------------------


def _primitive_clearance(category: CatalogCategory) -> Clearance:
    if category is CatalogCategory.TABLE:
        return Clearance(front=0.75, sides=0.1, back=0.05)
    if category is CatalogCategory.STORAGE:
        return Clearance(front=0.5, sides=0.05, back=0.02)
    if category is CatalogCategory.BED:
        return Clearance(front=0.6, sides=0.3, back=0.02)
    return Clearance(front=0.3, sides=0.05, back=0.02)


def _primitives(spec: SpecBase) -> FamilyBuild:
    """Compose the model's box list into one GLB.

    The spec validator already guarantees ground contact, part count and part
    bounds; this builder adds the whole-object envelope and the degenerate check
    that only makes sense once every part is placed.
    """
    if not isinstance(spec, PrimitivesSpec):
        raise ValueError("primitives family received a non-primitives spec")
    builder = ModelBuilder()
    for index, part in enumerate(spec.parts):
        sx, sy, sz = part.size
        cx, cy, cz = part.position
        try:
            builder.add_box(
                part.material,
                (cx - sx / 2, cy - sy / 2, cz - sz / 2),
                (cx + sx / 2, cy + sy / 2, cz + sz / 2),
            )
        except ValueError as error:
            raise ValueError(f"part {index}: {error}") from error

    builder.recenter()
    low, high = builder.bounds()
    measured = (high[0] - low[0], high[1] - low[1], high[2] - low[2])
    if max(measured) > MAX_PRIMITIVE_EXTENT:
        raise ValueError(
            f"composed object is {max(measured):.2f} m on its longest axis "
            f"(max {MAX_PRIMITIVE_EXTENT} m)"
        )
    if min(measured) < MIN_PRIMITIVE_EXTENT:
        raise ValueError(
            f"composed object is only {min(measured):.3f} m thick; add volume so it "
            "does not render as a zero-thickness slab"
        )

    parts = builder.build()
    document = build_glb_document(parts, name=spec.name)
    return FamilyBuild(
        document=document,
        expected_size_m=None,
        category=spec.category,
        tags=("primitives", "组合", "custom", spec.category.value),
        must_against_wall=spec.must_against_wall,
        clearance=_primitive_clearance(spec.category),
        allowed_rotations=ROTATIONS,
        render_parts=parts,
    )


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

_BUILDERS: dict[str, Callable[[SpecBase, ModelPathResolver | None], FamilyBuild]] = {
    "bookshelf": lambda spec, _: _bookshelf(spec),  # type: ignore[arg-type]
    "wardrobe": lambda spec, _: _wardrobe(spec),  # type: ignore[arg-type]
    "tv_stand": lambda spec, _: _tv_stand(spec),  # type: ignore[arg-type]
    "desk_l": lambda spec, _: _desk_l(spec),  # type: ignore[arg-type]
    "counter_run": lambda spec, _: _counter_run(spec),  # type: ignore[arg-type]
    "scale": _scale,
    "primitives": lambda spec, _: _primitives(spec),
}


def build_family(
    spec: SpecBase, *, resolve_model_path: ModelPathResolver | None = None
) -> FamilyBuild:
    builder = _BUILDERS.get(spec.family)
    if builder is None:
        raise ValueError(f"unknown family {spec.family!r}")
    return builder(spec, resolve_model_path)

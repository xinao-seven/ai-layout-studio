"""Parametric furniture specs: the editable inputs of generated assets.

Every spec is a Pydantic model with tight numeric bounds, so the same model
serves three purposes at once: OpenAI tool schema, L0 validation, and the
deterministic geometry input. The spec hash is the asset identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts.base import CamelModel
from app.contracts.enums import CatalogCategory

MaterialPreset = Literal["oak", "walnut", "white", "graphite"]
PrimitiveMaterial = Literal["oak", "walnut", "white", "graphite", "glass", "metal"]

FAMILY_LABELS: dict[str, str] = {
    "bookshelf": "书柜",
    "wardrobe": "衣柜",
    "tv_stand": "电视柜",
    "desk_l": "L型书桌",
    "counter_run": "橱柜",
    "scale": "缩放件",
    "primitives": "组合件",
}


class SpecBase(CamelModel):
    """Common behaviour for every generated family."""

    family: str = ""

    def size_m(self) -> tuple[float, float, float]:
        """Declared ``[width, depth, height]`` in metres."""
        raise NotImplementedError

    def display_name(self) -> str:
        width, depth, height = self.size_m()
        label = FAMILY_LABELS.get(self.family, self.family)
        return f"定制{label} {width:.2f}×{depth:.2f}×{height:.2f}"


class BookshelfSpec(SpecBase):
    family: Literal["bookshelf"]
    width_m: float = Field(default=1.2, ge=0.4, le=3.0, description="外宽, metres")
    depth_m: float = Field(default=0.32, ge=0.18, le=0.6, description="进深, metres")
    height_m: float = Field(default=2.0, ge=0.6, le=2.8, description="总高, metres")
    shelves: int = Field(default=4, ge=1, le=8, description="层板数量 (不含顶底板)")
    door_count: int = Field(default=0, ge=0, le=4, description="柜门扇数, 0 表示开放")
    glass_doors: bool = Field(default=False, description="柜门是否用玻璃")
    material: MaterialPreset = "oak"

    def size_m(self) -> tuple[float, float, float]:
        return (self.width_m, self.depth_m, self.height_m)


class WardrobeSpec(SpecBase):
    family: Literal["wardrobe"]
    width_m: float = Field(default=1.8, ge=0.6, le=3.6)
    depth_m: float = Field(default=0.6, ge=0.45, le=0.75)
    height_m: float = Field(default=2.2, ge=1.6, le=2.8)
    door_count: int = Field(default=2, ge=1, le=4)
    door_type: Literal["hinged", "sliding"] = "hinged"
    with_rail: bool = Field(default=True, description="是否含挂衣杆")
    material: MaterialPreset = "white"

    def size_m(self) -> tuple[float, float, float]:
        return (self.width_m, self.depth_m, self.height_m)


class TvStandSpec(SpecBase):
    family: Literal["tv_stand"]
    width_m: float = Field(default=1.6, ge=0.8, le=2.6)
    depth_m: float = Field(default=0.42, ge=0.3, le=0.55)
    height_m: float = Field(default=0.45, ge=0.3, le=0.75)
    drawers: int = Field(default=2, ge=0, le=3)
    open_shelf: bool = Field(default=True)
    material: MaterialPreset = "walnut"

    def size_m(self) -> tuple[float, float, float]:
        return (self.width_m, self.depth_m, self.height_m)


class DeskLSpec(SpecBase):
    family: Literal["desk_l"]
    width_m: float = Field(default=1.6, ge=1.0, le=2.2, description="主台面长度 (X)")
    depth_m: float = Field(default=0.65, ge=0.5, le=0.8, description="主台面进深 (Z)")
    return_width_m: float = Field(
        default=0.5, ge=0.35, le=0.9, description="回字边长, 沿 -Z 方向延伸的宽度 (X)"
    )
    return_length_m: float = Field(default=1.2, ge=0.8, le=1.6, description="回字长度 (Z)")
    height_m: float = Field(default=0.74, ge=0.68, le=0.78)
    drawer_count: int = Field(default=1, ge=0, le=3)
    material: MaterialPreset = "oak"

    def size_m(self) -> tuple[float, float, float]:
        return (self.width_m, self.depth_m + self.return_length_m, self.height_m)


class CounterRunSpec(SpecBase):
    family: Literal["counter_run"]
    length_m: float = Field(default=2.4, ge=0.9, le=4.0)
    depth_m: float = Field(default=0.6, ge=0.5, le=0.7)
    height_m: float = Field(default=0.9, ge=0.8, le=0.95)
    segments: int = Field(default=3, ge=1, le=4, description="柜体分段数")
    sink: bool = Field(default=False)
    cooktop: bool = Field(default=False)
    material: MaterialPreset = "white"

    def size_m(self) -> tuple[float, float, float]:
        return (self.length_m, self.depth_m, self.height_m)


class ScaleSpec(SpecBase):
    """Re-scale an existing catalog model (T1: variant by proportion)."""

    family: Literal["scale"]
    source_kind_id: str = Field(min_length=1, max_length=128, description="已存在的 kindId")
    factor_x: float = Field(default=1.0, ge=0.6, le=1.4, description="宽度倍数")
    factor_y: float = Field(default=1.0, ge=0.6, le=1.4, description="高度倍数")
    factor_z: float = Field(default=1.0, ge=0.6, le=1.4, description="进深倍数")

    def size_m(self) -> tuple[float, float, float]:
        # Actual size is read back from the scaled GLB by the compiler.
        return (0.0, 0.0, 0.0)

    def display_name(self) -> str:
        return f"{self.source_kind_id} ×{self.factor_x:.2f}/{self.factor_y:.2f}/{self.factor_z:.2f}"


PartLength = Annotated[float, Field(ge=0.01, le=3.0, allow_inf_nan=False)]
PartCoord = Annotated[float, Field(ge=-5.0, le=5.0, allow_inf_nan=False)]

MAX_PRIMITIVE_PARTS = 24


class PrimitivePart(CamelModel):
    """One axis-aligned box in item-local metres.

    ``position`` is the part centre; ``y = 0`` is the floor plane, so a
    1.6 m leg standing on the floor is ``size [0.04, 1.6, 0.04]`` at
    ``position [x, 0.8, z]``.
    """

    shape: Literal["box"] = "box"
    material: PrimitiveMaterial = "oak"
    size: tuple[PartLength, PartLength, PartLength] = Field(
        description="Box width/depth/height in metres, 0.01-3.0."
    )
    position: tuple[PartCoord, PartCoord, PartCoord] = Field(
        default=(0.0, 0.0, 0.0),
        description="Box centre relative to the item origin; y=0 is the floor.",
    )


class PrimitivesSpec(SpecBase):
    """Compose a simple orthogonal object from boxes.

    The long-tail escape hatch: use it for shapes no curated family covers
    (a drying rack, shoe rack, plant stand, partition), not for upholstered or
    curved furniture. The overall footprint is measured from the parts, so the
    model never has to declare a size it cannot guarantee.
    """

    family: Literal["primitives"]
    name: str = Field(min_length=1, max_length=60, description="显示名称, 如「落地晾衣架」")
    category: CatalogCategory = Field(
        default=CatalogCategory.OTHER,
        description=(
            "Closest catalog category. Drives placement rules: storage/table/bed "
            "count as circulation blockers, decor/other do not."
        ),
    )
    parts: list[PrimitivePart] = Field(
        min_length=1,
        max_length=MAX_PRIMITIVE_PARTS,
        description="1-24 boxes; at least one must touch the floor.",
    )
    must_against_wall: bool = Field(
        default=False, description="True when the object only makes sense against a wall."
    )

    def size_m(self) -> tuple[float, float, float]:
        # Measured from the composed parts by the compiler.
        return (0.0, 0.0, 0.0)

    def display_name(self) -> str:
        return self.name

    @model_validator(mode="after")
    def _check_grounded(self) -> PrimitivesSpec:
        lowest = min(part.position[1] - part.size[1] / 2 for part in self.parts)
        if lowest < -1e-6:
            raise ValueError("至少有一个部件低于地面; 把 position[1] 抬高到 size[1] / 2 以上")
        if lowest > 0.02:
            raise ValueError("没有部件接触地面; 至少有一个部件的底面应在 y≈0")
        return self


FamilySpec = Annotated[
    BookshelfSpec
    | WardrobeSpec
    | TvStandSpec
    | DeskLSpec
    | CounterRunSpec
    | ScaleSpec
    | PrimitivesSpec,
    Field(discriminator="family"),
]

FAMILY_NAMES: tuple[str, ...] = (
    "bookshelf",
    "wardrobe",
    "tv_stand",
    "desk_l",
    "counter_run",
    "scale",
    "primitives",
)


def canonical_spec(spec: SpecBase) -> dict[str, object]:
    return spec.model_dump(mode="json", by_alias=True, exclude_none=True)


def spec_hash(spec: SpecBase) -> str:
    """Stable 12-hex-digit identity for a spec (content address).

    12 hex chars keep the collision probability negligible for a personal asset
    library while staying short enough for a readable kind id.
    """
    payload = json.dumps(canonical_spec(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def kind_id_for(spec: SpecBase, hash8: str) -> str:
    return f"lib_{spec.family}_{hash8}"

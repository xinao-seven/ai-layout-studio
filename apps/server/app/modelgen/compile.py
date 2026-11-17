"""Spec -> validated GLB + thumbnail + catalog entry.

Compilation is deterministic and idempotent: the spec hash is the cache key, so
asking for the same furniture twice reuses the existing asset instead of writing
a second file. Every output is read back through ``glb_stats`` and checked
against the declared size, triangle and byte budgets before it is registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.catalog.assets import MAX_MODEL_BYTES, MAX_TRIANGLES
from app.catalog.glb import GlbStats, glb_stats, write_glb
from app.catalog.library import (
    AssetLibrary,
    AssetRecord,
    tmp_model_url,
    tmp_thumbnail_url,
)
from app.contracts.catalog import (
    CatalogCollision,
    CatalogEntry,
    CatalogLicense,
    CatalogVariant,
)
from app.modelgen.families import FamilyBuild, ModelPathResolver, build_family
from app.modelgen.specs import SpecBase, canonical_spec, kind_id_for, spec_hash
from app.modelgen.thumbnails import render_thumbnail

SIZE_TOLERANCE = 0.003
ORIGIN_TOLERANCE = 0.003


class CompileError(ValueError):
    """Raised when a spec cannot be turned into a valid asset."""


@dataclass
class CompiledAsset:
    entry: CatalogEntry
    spec_hash: str
    model_path: Path
    thumbnail_path: Path
    created: bool
    promoted: bool


def compile_spec(
    spec: SpecBase,
    library: AssetLibrary,
    *,
    resolve_model_path: ModelPathResolver | None = None,
) -> CompiledAsset:
    hash8 = spec_hash(spec)
    kind_id = kind_id_for(spec, hash8)

    existing = library.get(kind_id)
    if existing is not None:
        path = library.asset_path(existing)
        if path is not None:
            # Guard against a truncated-hash collision: a cache hit must be the
            # same spec, not merely the same id.
            stored = existing.generator.get("spec")
            if isinstance(stored, dict) and stored != canonical_spec(spec):
                raise CompileError(
                    f"spec hash collision for {kind_id}: stored spec differs"
                )
            return CompiledAsset(
                entry=existing.entry,
                spec_hash=hash8,
                model_path=path,
                thumbnail_path=(
                    library.thumbnail_path(hash8)
                    if existing.promoted
                    else library.tmp_thumbnail_path(hash8)
                ),
                created=False,
                promoted=existing.promoted,
            )

    build = build_family(spec, resolve_model_path=resolve_model_path)
    library.ensure_dirs()
    model_path = library.tmp_model_path(hash8)
    thumbnail_path = library.tmp_thumbnail_path(hash8)
    try:
        write_glb(model_path, build.document)
        stats = glb_stats(model_path)
        size_m = _validate(build, stats, model_path)
        render_thumbnail(build.render_parts, thumbnail_path, size_m=size_m)
    except Exception:
        model_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        raise

    entry = _entry_for(spec, build, kind_id=kind_id, hash8=hash8, size_m=size_m)
    library.register(
        AssetRecord(
            entry=entry,
            generator={
                "family": spec.family,
                "spec": canonical_spec(spec),
                "specHash": hash8,
            },
            promoted=False,
        )
    )
    return CompiledAsset(
        entry=entry,
        spec_hash=hash8,
        model_path=model_path,
        thumbnail_path=thumbnail_path,
        created=True,
        promoted=False,
    )


def _validate(
    build: FamilyBuild, stats: GlbStats, path: Path
) -> tuple[float, float, float]:
    triangles = stats.triangles
    size = stats.size
    minimum = stats.minimum
    if triangles > MAX_TRIANGLES:
        raise CompileError(f"model has {triangles} triangles (max {MAX_TRIANGLES})")
    if path.stat().st_size > MAX_MODEL_BYTES:
        raise CompileError(f"model is {path.stat().st_size} bytes (max {MAX_MODEL_BYTES})")
    if abs(minimum[1]) > ORIGIN_TOLERANCE:
        raise CompileError(f"model is not bottom-centred (minY={minimum[1]:.3f})")
    # glb_stats reports (x, y, z); catalog sizeM is [width, depth, height].
    measured = (size[0], size[2], size[1])
    if build.expected_size_m is not None:
        for axis, (declared, actual) in enumerate(
            zip(build.expected_size_m, measured, strict=True)
        ):
            if abs(declared - actual) > SIZE_TOLERANCE:
                raise CompileError(
                    f"size mismatch on axis {axis}: expected {declared:.3f}, measured {actual:.3f}"
                )
    if min(measured) <= 0:
        raise CompileError(f"degenerate asset size {measured}")
    return measured


def _entry_for(
    spec: SpecBase,
    build: FamilyBuild,
    *,
    kind_id: str,
    hash8: str,
    size_m: tuple[float, float, float],
) -> CatalogEntry:
    return CatalogEntry(
        kind_id=kind_id,
        name=spec.display_name(),
        category=build.category,
        tags=list(build.tags),
        placement="floor",
        default_variant_id="default",
        variants=[
            CatalogVariant(
                id="default",
                size_m=[round(value, 4) for value in size_m],
                model_path=tmp_model_url(hash8),
                thumbnail_path=tmp_thumbnail_url(hash8),
            )
        ],
        front_axis="+Z",
        pivot="bottom-center",
        collision=CatalogCollision(),
        clearance=build.clearance,
        allowed_rotations=list(build.allowed_rotations),
        rules=build.rules(),
        source="library",
        license=CatalogLicense(
            name="generated:parametric",
            url=f"internal://modelgen/{spec.family}",
        ),
    )
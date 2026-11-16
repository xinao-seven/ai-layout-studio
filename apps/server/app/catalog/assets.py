"""Catalog asset validation shared by the build script and tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from app.catalog.glb import glb_stats

MIN_CATALOG_ENTRIES = 30
MAX_CATALOG_ENTRIES = 60
MAX_MODEL_BYTES = 1024 * 1024
MAX_TRIANGLES = 15_000
THUMBNAIL_SIZE = 256
SIZE_TOLERANCE = 0.003
REQUIRED_KIND_IDS = {
    "sofa",
    "coffee_table",
    "bed_double",
    "bed_single",
    "wardrobe",
    "nightstand",
    "tv_stand",
    "rug",
    "dining_table",
    "desk",
    "bookshelf",
    "chair",
    "plant",
}


def _resolve_asset(assets_root: Path, asset_path: str) -> Path | None:
    prefix = "/assets/"
    if not asset_path.startswith(prefix):
        return None
    relative = Path(asset_path[len(prefix) :])
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (assets_root / relative).resolve()
    try:
        candidate.relative_to(assets_root.resolve())
    except ValueError:
        return None
    return candidate


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_catalog_assets(catalog_path: Path, assets_root: Path) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    try:
        catalog = _load_json(catalog_path)
        entries = catalog["entries"]
    except (OSError, ValueError, KeyError) as error:
        return [f"cannot read catalog {catalog_path}: {error}"]

    if not isinstance(entries, list):
        return [f"{catalog_path} must contain an entries array"]
    if not MIN_CATALOG_ENTRIES <= len(entries) <= MAX_CATALOG_ENTRIES:
        errors.append(
            f"catalog has {len(entries)} entries; expected {MIN_CATALOG_ENTRIES}–"
            f"{MAX_CATALOG_ENTRIES}"
        )

    kind_ids = [entry.get("kindId") for entry in entries]
    if len(kind_ids) != len(set(kind_ids)):
        errors.append("catalog contains duplicate kindIds")
    missing = sorted(REQUIRED_KIND_IDS - set(kind_ids))
    if missing:
        errors.append(f"catalog is missing required fixture kinds: {missing}")

    index: dict[str, dict[str, Any]] = {}
    index_path = assets_root / "generated" / "assets-index.json"
    if index_path.is_file():
        try:
            index = {
                row["kindId"]: row
                for row in _load_json(index_path).get("assets", [])
                if isinstance(row, dict) and "kindId" in row
            }
        except (OSError, ValueError) as error:
            errors.append(f"cannot read asset index {index_path}: {error}")
    else:
        errors.append(f"missing asset index {index_path}")

    for entry in entries:
        kind_id = entry.get("kindId")
        if not isinstance(kind_id, str) or not kind_id:
            errors.append("catalog entry has no kindId")
            continue
        if entry.get("frontAxis") != "+Z":
            errors.append(f"{kind_id}: frontAxis must be +Z")
        if entry.get("pivot") != "bottom-center":
            errors.append(f"{kind_id}: pivot must be bottom-center")
        license_info = entry.get("license")
        if not isinstance(license_info, dict) or not license_info.get("name"):
            errors.append(f"{kind_id}: missing license metadata")
        variants = entry.get("variants") or []
        if not variants:
            errors.append(f"{kind_id}: no variants")
            continue
        for variant in variants:
            model_path = _resolve_asset(assets_root, str(variant.get("modelPath", "")))
            thumb_path = _resolve_asset(assets_root, str(variant.get("thumbnailPath", "")))
            if model_path is None or not model_path.is_file():
                errors.append(f"{kind_id}: missing model {variant.get('modelPath')!r}")
                continue
            if thumb_path is None or not thumb_path.is_file():
                errors.append(f"{kind_id}: missing thumbnail {variant.get('thumbnailPath')!r}")
            model_bytes = model_path.stat().st_size
            if model_bytes > MAX_MODEL_BYTES:
                errors.append(f"{kind_id}: model is {model_bytes} bytes (max {MAX_MODEL_BYTES})")
            try:
                stats = glb_stats(model_path)
            except ValueError as error:
                errors.append(f"{kind_id}: invalid GLB: {error}")
                continue
            if stats.triangles > MAX_TRIANGLES:
                errors.append(f"{kind_id}: {stats.triangles} triangles (max {MAX_TRIANGLES})")
            declared = variant.get("sizeM")
            if not isinstance(declared, list) or len(declared) != 3:
                errors.append(f"{kind_id}: sizeM must be a 3-vector")
            else:
                # catalog sizeM is [width, depth, height]; GLB stats are [x, y, z].
                expected = (stats.size[0], stats.size[2], stats.size[1])
                for axis, value in enumerate(expected):
                    if abs(float(declared[axis]) - value) > SIZE_TOLERANCE:
                        errors.append(
                            f"{kind_id}: declared sizeM[{axis}]={declared[axis]} but asset "
                            f"measures {value:.3f}"
                        )
                if abs(stats.minimum[1]) > SIZE_TOLERANCE:
                    errors.append(
                        f"{kind_id}: asset is not bottom-centered (minY={stats.minimum[1]})"
                    )
            if thumb_path is not None and thumb_path.is_file():
                with Image.open(thumb_path) as image:
                    if image.size != (THUMBNAIL_SIZE, THUMBNAIL_SIZE):
                        errors.append(
                            f"{kind_id}: thumbnail is {image.size}, expected "
                            f"({THUMBNAIL_SIZE}, {THUMBNAIL_SIZE})"
                        )
            indexed = index.get(kind_id)
            if indexed is not None:
                if indexed.get("triangles") != stats.triangles:
                    errors.append(f"{kind_id}: asset index triangle count is stale")
                if indexed.get("bytes") != model_bytes:
                    errors.append(f"{kind_id}: asset index byte count is stale")
                digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
                if indexed.get("sha256") != digest:
                    errors.append(f"{kind_id}: asset index sha256 is stale")
                if indexed.get("sizeM") and any(
                    abs(
                        float(indexed["sizeM"][axis])
                        - (stats.size[0], stats.size[2], stats.size[1])[axis]
                    )
                    > SIZE_TOLERANCE
                    for axis in range(3)
                ):
                    errors.append(f"{kind_id}: asset index size is stale")
            else:
                errors.append(f"{kind_id}: missing from asset index")
    return errors

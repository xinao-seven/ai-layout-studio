#!/usr/bin/env python3
"""Build normalized furniture assets and ``fixtures/catalog.json``.

Source: Kenney Furniture Kit (CC0). The downloaded archive is not committed;
pass the extracted pack directory (containing ``Models/GLTF format`` and
``Isometric``) with ``--source``. Generated GLBs, thumbnails and the catalog are
committed so the app runs without network access.

Usage:
    uv run --directory apps/server python ../../scripts/build_catalog_assets.py \
        --source /path/to/kenney-furniture-kit

    # verify the committed artifacts without the source pack
    uv run --directory apps/server python ../../scripts/build_catalog_assets.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from app.catalog.assets import validate_catalog_assets  # noqa: E402
from app.catalog.glb import glb_stats, read_glb, write_glb  # noqa: E402

MANIFEST = REPO_ROOT / "assets" / "source" / "catalog-manifest.json"
ASSETS_ROOT = REPO_ROOT / "assets"
MODELS_DIR = ASSETS_ROOT / "models"
THUMBNAILS_DIR = ASSETS_ROOT / "thumbnails"
INDEX_PATH = ASSETS_ROOT / "generated" / "assets-index.json"
CATALOG_PATH = REPO_ROOT / "fixtures" / "catalog.json"

THUMBNAIL_SIZE = 256
THUMBNAIL_PADDING = 18
THUMBNAIL_BACKGROUND = "#1f1f1f"
MAX_MODEL_BYTES = 1024 * 1024
MAX_TRIANGLES = 15_000


def _target_scale(entry: dict[str, Any], size: tuple[float, float, float]) -> float:
    target = entry["size"]
    mode = target["mode"]
    value = target["value"]
    if mode == "height":
        return float(value) / size[1]
    if mode == "length":
        return float(value) / max(size[0], size[2])
    if mode == "width":
        return float(value) / size[0]
    if mode == "footprint":
        target_x, target_z = (float(part) for part in value)
        return min(target_x / size[0], target_z / size[2])
    if mode == "scale":
        return float(value)
    raise ValueError(f"unknown size mode {mode!r} for {entry['kindId']}")


def _normalize_model(source: Path, destination: Path, scale: float) -> tuple[float, float, float]:
    """Bake a uniform scale and a bottom-center pivot into the GLB."""
    document = read_glb(source)
    stats = glb_stats(source)
    minimum = stats.minimum
    center_x = minimum[0] + stats.size[0] / 2.0
    center_z = minimum[2] + stats.size[2] / 2.0

    payload = document.json
    scene_index = payload.get("scene", 0)
    scenes = payload.setdefault("scenes", [{"nodes": []}])
    roots = list(scenes[scene_index].get("nodes", []))
    if not roots:
        roots = list(range(len(payload.get("nodes", []))))
    wrapper_index = len(payload.get("nodes", []))
    payload.setdefault("nodes", []).append(
        {
            "name": "__catalog_normalize",
            "children": roots,
            "scale": [scale, scale, scale],
            "translation": [
                -center_x * scale,
                -minimum[1] * scale,
                -center_z * scale,
            ],
        }
    )
    scenes[scene_index]["nodes"] = [wrapper_index]
    normalized = type(document)(json=payload, binary=document.binary, extra_chunks=document.extra_chunks)
    write_glb(destination, normalized)
    final = glb_stats(destination)
    normalized_size = tuple(round(value, 3) for value in final.size)
    if any(value <= 0 for value in normalized_size):
        raise ValueError(f"normalized {source.name} has a degenerate size {normalized_size}")
    if abs(final.minimum[1]) > 0.002:
        raise ValueError(f"normalized {source.name} pivot is not on the floor: {final.minimum}")
    return (normalized_size[0], normalized_size[1], normalized_size[2])


def _build_thumbnail(source: Path, destination: Path) -> None:
    image = Image.open(source).convert("RGBA")
    canvas = Image.new("RGBA", (THUMBNAIL_SIZE, THUMBNAIL_SIZE), THUMBNAIL_BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(
        (THUMBNAIL_SIZE * 0.18, THUMBNAIL_SIZE * 0.72, THUMBNAIL_SIZE * 0.82, THUMBNAIL_SIZE * 0.9),
        fill="#181818",
    )
    available = THUMBNAIL_SIZE - THUMBNAIL_PADDING * 2
    ratio = min(available / image.width, available / image.height)
    resized = image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    left = (THUMBNAIL_SIZE - resized.width) // 2
    top = (THUMBNAIL_SIZE - resized.height) // 2
    canvas.alpha_composite(resized, (left, top))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(destination, format="PNG", optimize=True)


def build(source_root: Path) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    models_dir = source_root / "Models" / "GLTF format"
    thumbnails_dir = source_root / "Isometric"
    if not models_dir.is_dir():
        raise SystemExit(f"missing source models directory: {models_dir}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        model_name = entry["model"]
        source_model = models_dir / f"{model_name}.glb"
        if not source_model.is_file():
            raise SystemExit(f"missing source model: {source_model}")
        stats = glb_stats(source_model)
        scale = _target_scale(entry, stats.size)
        destination_model = MODELS_DIR / f"{entry['kindId']}.glb"
        size = _normalize_model(source_model, destination_model, scale)
        final_stats = glb_stats(destination_model)
        model_bytes = destination_model.stat().st_size
        if model_bytes > MAX_MODEL_BYTES:
            raise SystemExit(f"{entry['kindId']} exceeds 1 MiB: {model_bytes} bytes")
        if final_stats.triangles > MAX_TRIANGLES:
            raise SystemExit(
                f"{entry['kindId']} has {final_stats.triangles} triangles (max {MAX_TRIANGLES})"
            )

        thumbnail_source = None
        for angle in ("SW", "SE", "NW", "NE"):
            candidate = thumbnails_dir / f"{model_name}_{angle}.png"
            if candidate.is_file():
                thumbnail_source = candidate
                break
        if thumbnail_source is None:
            raise SystemExit(f"missing thumbnail for {model_name}")
        destination_thumbnail = THUMBNAILS_DIR / f"{entry['kindId']}.png"
        _build_thumbnail(thumbnail_source, destination_thumbnail)

        catalog_size = [size[0], size[2], size[1]]
        catalog_entry = {
            "kindId": entry["kindId"],
            "name": entry["name"],
            "category": entry["category"],
            "tags": entry["tags"],
            "placement": "floor",
            "defaultVariantId": "default",
            "variants": [
                {
                    "id": "default",
                    "sizeM": catalog_size,
                    "modelPath": f"/assets/models/{entry['kindId']}.glb",
                    "thumbnailPath": f"/assets/thumbnails/{entry['kindId']}.png",
                }
            ],
            "frontAxis": "+Z",
            "pivot": "bottom-center",
            "collision": {"type": "obb"},
            "clearance": entry.get("clearance", {}),
            "allowedRotations": [0.0, 90.0, 180.0, 270.0],
            "rules": entry.get("rules"),
            "license": {
                "name": manifest["source"]["license"],
                "url": manifest["source"]["licenseUrl"],
            },
        }
        entries.append(catalog_entry)
        digest = hashlib.sha256(destination_model.read_bytes()).hexdigest()
        index.append(
            {
                "kindId": entry["kindId"],
                "sourceModel": model_name,
                "scale": round(scale, 6),
                "sizeM": catalog_size,
                "triangles": final_stats.triangles,
                "bytes": model_bytes,
                "sha256": digest,
                "thumbnail": destination_thumbnail.name,
            }
        )
        print(
            f"{entry['kindId']:24s} {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} m "
            f"{final_stats.triangles:5d} tris {model_bytes / 1024:5.1f} KiB"
        )

    catalog = {
        "schemaVersion": 1,
        "source": manifest["source"],
        "entries": entries,
    }
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    INDEX_PATH.write_text(
        json.dumps(
            {"schemaVersion": 1, "generatedBy": "scripts/build_catalog_assets.py", "assets": index},
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nwrote {len(entries)} catalog entries")
    errors = validate_catalog_assets(CATALOG_PATH, ASSETS_ROOT)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("catalog assets validated")
    return 0


def check() -> int:
    errors = validate_catalog_assets(CATALOG_PATH, ASSETS_ROOT)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))["assets"]
    except (OSError, ValueError, KeyError) as error:
        errors.append(f"cannot read manifest/catalog/index: {error}")
        manifest = {"entries": [], "source": {}}
        catalog = {"entries": []}
        index = []

    source_license = (manifest.get("source") or {}).get("license")
    manifest_entries = manifest.get("entries", [])
    catalog_entries = catalog.get("entries", [])
    index_by_kind = {row["kindId"]: row for row in index if "kindId" in row}
    manifest_ids = {entry.get("kindId") for entry in manifest_entries}
    catalog_ids = {entry.get("kindId") for entry in catalog_entries}
    if manifest_ids != catalog_ids:
        errors.append(
            "manifest and catalog kindIds differ: "
            f"missing={sorted(manifest_ids - catalog_ids)} extra={sorted(catalog_ids - manifest_ids)}"
        )
    if len(manifest_entries) != len(catalog_entries):
        errors.append(
            f"manifest has {len(manifest_entries)} entries, catalog has {len(catalog_entries)}"
        )
    for entry in manifest_entries:
        kind_id = entry.get("kindId")
        indexed = index_by_kind.get(kind_id)
        if indexed is None:
            errors.append(f"{kind_id}: manifest entry missing from asset index")
            continue
        if indexed.get("sourceModel") != entry.get("model"):
            errors.append(
                f"{kind_id}: manifest model {entry.get('model')!r} != index "
                f"{indexed.get('sourceModel')!r}"
            )
    for entry in catalog_entries:
        license_info = entry.get("license") or {}
        if source_license and license_info.get("name") != source_license:
            errors.append(
                f"{entry.get('kindId')}: catalog license {license_info.get('name')!r} "
                f"!= manifest source license {source_license!r}"
            )
    credits = ASSETS_ROOT / "CREDITS.md"
    if credits.is_file():
        text = credits.read_text(encoding="utf-8")
        pack = (manifest.get("source") or {}).get("pack", "")
        if pack and pack not in text:
            errors.append(f"CREDITS.md does not mention the source pack {pack!r}")
        if source_license and source_license not in text:
            errors.append(f"CREDITS.md does not mention the license {source_license!r}")
    else:
        errors.append("missing assets/CREDITS.md")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("catalog assets validated (manifest, catalog, index, credits)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="extracted Kenney Furniture Kit directory")
    parser.add_argument("--check", action="store_true", help="validate committed artifacts only")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.source is None:
        parser.error("--source is required unless --check is used")
    return build(args.source)


if __name__ == "__main__":
    raise SystemExit(main())

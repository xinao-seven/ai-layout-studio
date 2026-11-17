"""Runtime asset library for parametric furniture models.

Generated GLBs live outside the repository ``assets/`` tree so git checkouts and
container rebuilds cannot silently drop them:

    <root>/manifest.json         source of truth, one record per asset
    <root>/models/<hash>.glb     promoted (confirmed) models
    <root>/thumbnails/<hash>.png promoted thumbnails
    <root>/tmp/<hash>.glb        models owned by a pending proposal
    <root>/tmp/<hash>.png

Promoted assets are never garbage collected: a scene only stores ``kindId``, so
deleting a referenced asset would make that scene fail validation on load. Only
``tmp/`` is reclaimed, and only for records that never got promoted.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.contracts.catalog import CatalogEntry

logger = structlog.get_logger(__name__)

MANIFEST_VERSION = 1
TMP_SUBDIR = "tmp"
MODELS_SUBDIR = "models"
THUMBNAILS_SUBDIR = "thumbnails"
URL_PREFIX = "/assets/library"


def model_url(hash8: str) -> str:
    return f"{URL_PREFIX}/{MODELS_SUBDIR}/{hash8}.glb"


def thumbnail_url(hash8: str) -> str:
    return f"{URL_PREFIX}/{THUMBNAILS_SUBDIR}/{hash8}.png"


def tmp_model_url(hash8: str) -> str:
    return f"{URL_PREFIX}/{TMP_SUBDIR}/{hash8}.glb"


def tmp_thumbnail_url(hash8: str) -> str:
    return f"{URL_PREFIX}/{TMP_SUBDIR}/{hash8}.png"


def entry_with_urls(entry: CatalogEntry, hash8: str, *, promoted: bool) -> CatalogEntry:
    """Point a catalog entry's variants at the namespace matching its state."""
    model = model_url(hash8) if promoted else tmp_model_url(hash8)
    thumbnail = thumbnail_url(hash8) if promoted else tmp_thumbnail_url(hash8)
    variants = [
        variant.model_copy(update={"model_path": model, "thumbnail_path": thumbnail})
        for variant in entry.variants
    ]
    return entry.model_copy(update={"variants": variants})


def promoted_entry(entry: CatalogEntry, hash8: str) -> CatalogEntry:
    """Rewrite variant URLs from the temp namespace into the permanent one."""
    return entry_with_urls(entry, hash8, promoted=True)


@dataclass
class AssetRecord:
    """One generated asset plus the generator inputs that produced it."""

    entry: CatalogEntry
    generator: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    promoted: bool = False

    @property
    def spec_hash(self) -> str:
        return str(self.generator.get("specHash", ""))

    def to_json(self) -> dict[str, Any]:
        return {
            "entry": self.entry.model_dump(mode="json", by_alias=True),
            "generator": self.generator,
            "createdAt": self.created_at,
            "promoted": self.promoted,
        }

    @classmethod
    def from_json(cls, payload: Any) -> AssetRecord:
        if not isinstance(payload, dict):
            raise ValueError("asset record must be a JSON object")
        entry = CatalogEntry.model_validate(payload["entry"])
        generator = payload.get("generator") or {}
        if not isinstance(generator, dict):
            raise ValueError("asset record generator must be a JSON object")
        return cls(
            entry=entry,
            generator=generator,
            created_at=str(payload.get("createdAt", "")),
            promoted=bool(payload.get("promoted", False)),
        )


class AssetLibrary:
    """Manifest-backed store for generated assets (single process)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._manifest_path = root / "manifest.json"
        self._lock = threading.RLock()
        self._records: dict[str, AssetRecord] = {}
        self._load()

    # ---- paths -----------------------------------------------------------

    def tmp_model_path(self, hash8: str) -> Path:
        return self.root / TMP_SUBDIR / f"{hash8}.glb"

    def tmp_thumbnail_path(self, hash8: str) -> Path:
        return self.root / TMP_SUBDIR / f"{hash8}.png"

    def model_path(self, hash8: str) -> Path:
        return self.root / MODELS_SUBDIR / f"{hash8}.glb"

    def thumbnail_path(self, hash8: str) -> Path:
        return self.root / THUMBNAILS_SUBDIR / f"{hash8}.png"

    def asset_path(self, record: AssetRecord) -> Path | None:
        """Current on-disk GLB for a record, promoted or not."""
        hash8 = record.spec_hash
        if not hash8:
            return None
        candidate = self.model_path(hash8) if record.promoted else self.tmp_model_path(hash8)
        return candidate if candidate.is_file() else None

    def ensure_dirs(self) -> None:
        for subdir in (TMP_SUBDIR, MODELS_SUBDIR, THUMBNAILS_SUBDIR):
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    # ---- queries ---------------------------------------------------------

    @property
    def entries(self) -> list[CatalogEntry]:
        with self._lock:
            return [record.entry for record in self._records.values()]

    @property
    def by_kind(self) -> dict[str, CatalogEntry]:
        with self._lock:
            return {kind_id: record.entry for kind_id, record in self._records.items()}

    def get(self, kind_id: str) -> AssetRecord | None:
        with self._lock:
            return self._records.get(kind_id)

    def spec_for(self, kind_id: str) -> dict[str, Any] | None:
        record = self.get(kind_id)
        if record is None:
            return None
        spec = record.generator.get("spec")
        return dict(spec) if isinstance(spec, dict) else None

    # ---- mutation --------------------------------------------------------

    def register(self, record: AssetRecord) -> None:
        """Add or replace one record and persist the manifest."""
        with self._lock:
            if not record.created_at:
                record.created_at = datetime.now(UTC).isoformat()
            self._records[record.entry.kind_id] = record
            self._save_locked()

    def promote(self, kind_id: str) -> bool:
        """Move a pending asset into the permanent namespace.

        The file moves and the manifest update (including the permanent variant
        URLs) happen in one save, so a crash cannot leave a record pointing at
        the temp namespace after its file has moved.
        """
        with self._lock:
            record = self._records.get(kind_id)
            if record is None:
                return False
            if record.promoted:
                return True
            hash8 = record.spec_hash
            if not hash8:
                return False
            source = self.tmp_model_path(hash8)
            target = self.model_path(hash8)
            self.ensure_dirs()
            if source.is_file():
                os.replace(source, target)
                thumb_source = self.tmp_thumbnail_path(hash8)
                if thumb_source.is_file():
                    os.replace(thumb_source, self.thumbnail_path(hash8))
            elif not target.is_file():
                logger.info("library.promote_missing", kind_id=kind_id, hash=hash8)
                return False
            record.entry = promoted_entry(record.entry, hash8)
            record.promoted = True
            self._save_locked(strict=True)
            return True

    def release(self, kind_id: str) -> bool:
        """Forget an unpromoted record (used when a placement is refused)."""
        with self._lock:
            record = self._records.get(kind_id)
            if record is None or record.promoted:
                return False
            self._records.pop(kind_id, None)
            self._save_locked()
            return True

    def gc_tmp(self, ttl_hours: int, *, protected: frozenset[str] = frozenset()) -> list[str]:
        """Delete temp assets that no scene references.

        ``protected`` is the set of kind ids referenced by any persisted scene.
        Those are never reclaimed: a scene stores only a ``kindId``, so deleting
        the file would make the scene fail to load.
        """
        removed: list[str] = []
        tmp_dir = self.root / TMP_SUBDIR
        if not tmp_dir.is_dir():
            return removed
        cutoff = time.time() - max(0.0, ttl_hours) * 3600
        with self._lock:
            keep_hashes = {
                record.spec_hash
                for record in self._records.values()
                if record.spec_hash and (record.promoted or record.entry.kind_id in protected)
            }
            for path in tmp_dir.iterdir():
                if not path.is_file():
                    continue
                if path.stem in keep_hashes:
                    continue
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                    removed.append(path.name)
                except OSError as error:  # pragma: no cover - best effort
                    logger.info("library.gc_failed", path=str(path), error=str(error))
            # Drop records whose temp file disappeared without promotion.
            stale = [
                kind_id
                for kind_id, record in self._records.items()
                if not record.promoted
                and record.entry.kind_id not in protected
                and record.spec_hash
                and not self.tmp_model_path(record.spec_hash).is_file()
            ]
            for kind_id in stale:
                self._records.pop(kind_id, None)
            if stale:
                self._save_locked()
        return removed

    # ---- persistence -----------------------------------------------------

    def _load(self) -> None:
        self.ensure_dirs()
        if not self._manifest_path.is_file():
            return
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            raw_records = payload.get("assets", []) if isinstance(payload, dict) else payload
        except (OSError, ValueError) as error:
            logger.warning(
                "library.manifest_unreadable", path=str(self._manifest_path), error=str(error)
            )
            return
        for raw in raw_records:
            try:
                record = AssetRecord.from_json(raw)
            except (KeyError, ValueError, TypeError) as error:
                logger.warning("library.record_invalid", error=str(error))
                continue
            hash8 = record.spec_hash
            if not hash8:
                logger.warning("library.record_without_hash", kind_id=record.entry.kind_id)
                continue
            promoted_file = self.model_path(hash8).is_file()
            temp_file = self.tmp_model_path(hash8).is_file()
            if not promoted_file and not temp_file:
                logger.warning("library.asset_missing", kind_id=record.entry.kind_id)
                continue
            # Reconcile the record with what is actually on disk: a crash
            # between the file move and the manifest write must not leave the
            # entry pointing at a namespace whose file is gone.
            if promoted_file:
                if not record.promoted:
                    logger.warning("library.repaired_promotion", kind_id=record.entry.kind_id)
                    record.promoted = True
                record.entry = entry_with_urls(record.entry, hash8, promoted=True)
            else:
                if record.promoted:
                    logger.warning("library.repaired_demotion", kind_id=record.entry.kind_id)
                    record.promoted = False
                record.entry = entry_with_urls(record.entry, hash8, promoted=False)
            self._records[record.entry.kind_id] = record
        logger.info("library.loaded", assets=len(self._records))

    def _save_locked(self, *, strict: bool = False) -> None:
        self.ensure_dirs()
        payload = {
            "schemaVersion": MANIFEST_VERSION,
            "assets": [record.to_json() for record in self._records.values()],
        }
        temporary = self._manifest_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self._manifest_path)
        except OSError as error:
            logger.warning("library.manifest_write_failed", error=str(error))
            if strict:
                raise

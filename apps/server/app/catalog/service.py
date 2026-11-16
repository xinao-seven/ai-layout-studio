"""Minimal catalog service.

The full 30–60 item catalog and asset normalization land in M2-01. M1 only
needs enough metadata to validate and render the built-in fixtures with
wireframe fallback boxes.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.contracts.catalog import CatalogEntry
from app.core.paths import project_roots
from app.scene.errors import SceneError


def find_catalog_path() -> Path:
    candidates = [root / "fixtures" / "catalog.json" for root in project_roots()]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"catalog.json not found; searched: {candidates}")


class CatalogService:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or find_catalog_path()
        self._entries = self._load(self._path)
        self._by_kind = {entry.kind_id: entry for entry in self._entries}

    @staticmethod
    def _load(path: Path) -> list[CatalogEntry]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_entries = payload["entries"] if isinstance(payload, dict) else payload
            entries = [CatalogEntry.model_validate(entry) for entry in raw_entries]
        except (OSError, ValueError, KeyError) as error:
            raise SceneError(
                "invalid_input",
                f"Catalog {path} could not be loaded.",
                "Fix the catalog JSON and reload it.",
                {"error": str(error)},
            ) from error
        kind_ids = [entry.kind_id for entry in entries]
        if len(kind_ids) != len(set(kind_ids)):
            raise SceneError(
                "invalid_input",
                "Catalog contains duplicate kind ids.",
                "Give every kind a unique kindId.",
            )
        return entries

    @property
    def entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    @property
    def by_kind(self) -> dict[str, CatalogEntry]:
        return dict(self._by_kind)

    def get(self, kind_id: str) -> CatalogEntry | None:
        return self._by_kind.get(kind_id)

    def preview(self, path: Path | None = None) -> list[CatalogEntry]:
        """Load a catalog without mutating the live service."""
        return self._load(path or self._path)

    def apply(self, entries: list[CatalogEntry], path: Path | None = None) -> list[CatalogEntry]:
        self._path = path or self._path
        self._entries = entries
        self._by_kind = {entry.kind_id: entry for entry in entries}
        return list(entries)

    def reload(self, path: Path | None = None) -> list[CatalogEntry]:
        new_path = path or self._path
        entries = self._load(new_path)
        return self.apply(entries, new_path)

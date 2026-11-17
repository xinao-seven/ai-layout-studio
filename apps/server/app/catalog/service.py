"""Catalog service: shipped fixtures merged with the generated asset library.

The fixture JSON stays the only built-in source (30–60 entries, validated at
build time). Generated assets live in :class:`~app.catalog.library.AssetLibrary`
and are merged into the live view here, so every consumer (scene validation,
``search_catalog``, the solver, the agent system prompt) sees one catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.catalog.library import AssetLibrary
from app.contracts.catalog import CatalogEntry
from app.core.paths import project_roots
from app.scene.errors import SceneError

logger = structlog.get_logger(__name__)


def find_catalog_path() -> Path:
    candidates = [root / "fixtures" / "catalog.json" for root in project_roots()]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"catalog.json not found; searched: {candidates}")


class CatalogService:
    def __init__(self, path: Path | None = None, *, library: AssetLibrary | None = None) -> None:
        self._path = path or find_catalog_path()
        self._library = library
        self._builtin = self._load(self._path)
        self._entries: list[CatalogEntry] = []
        self._by_kind: dict[str, CatalogEntry] = {}
        self._rebuild()

    # ---- loading ---------------------------------------------------------

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

    @staticmethod
    def _merge(
        builtin: list[CatalogEntry],
        generated: dict[str, CatalogEntry],
    ) -> list[CatalogEntry]:
        """Built-in entries win any kindId collision; duplicates are dropped."""
        entries = list(builtin)
        seen = {entry.kind_id for entry in entries}
        for kind_id, entry in generated.items():
            if kind_id in seen:
                logger.warning("catalog.kind_collision", kind_id=kind_id, source="library")
                continue
            entries.append(entry)
            seen.add(kind_id)
        return entries

    def _rebuild(self) -> None:
        generated = self._library.by_kind if self._library is not None else {}
        self._entries = self._merge(self._builtin, generated)
        self._by_kind = {entry.kind_id: entry for entry in self._entries}

    # ---- queries ---------------------------------------------------------

    @property
    def entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    @property
    def by_kind(self) -> dict[str, CatalogEntry]:
        return dict(self._by_kind)

    @property
    def library(self) -> AssetLibrary | None:
        return self._library

    @property
    def builtin_count(self) -> int:
        return len(self._builtin)

    def get(self, kind_id: str) -> CatalogEntry | None:
        return self._by_kind.get(kind_id)

    def preview(self, path: Path | None = None) -> list[CatalogEntry]:
        """Load built-in entries from disk without mutating the live service."""
        return self._load(path or self._path)

    def preview_merged(self, path: Path | None = None) -> list[CatalogEntry]:
        """Built-in preview plus the current library, as the live view would be."""
        generated = self._library.by_kind if self._library is not None else {}
        return self._merge(self.preview(path), generated)

    # ---- mutation --------------------------------------------------------

    def apply(self, entries: list[CatalogEntry], path: Path | None = None) -> list[CatalogEntry]:
        self._path = path or self._path
        self._builtin = entries
        self._rebuild()
        return self.entries

    def reload(self, path: Path | None = None) -> list[CatalogEntry]:
        return self.apply(self._load(path or self._path), path)

    def refresh_library(self) -> list[CatalogEntry]:
        """Re-merge after the asset library changed (new generated asset)."""
        self._rebuild()
        return self.entries

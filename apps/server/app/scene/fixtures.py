"""Built-in floor-plan fixtures (PRD FR-01)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.contracts.api import FixtureSummary
from app.contracts.scene import SceneDocument
from app.core.paths import project_roots
from app.geometry.polygon import polygon_area
from app.scene.errors import SceneError
from app.scene.factory import scene_from_fixture


def find_fixtures_root() -> Path:
    candidates = [root / "fixtures" for root in project_roots()]
    for candidate in candidates:
        if (candidate / "fixtures.json").is_file():
            return candidate
    raise FileNotFoundError(f"fixtures.json not found; searched: {candidates}")


class FixtureService:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or find_fixtures_root()
        self._index = self._load_index()
        self._documents: dict[str, SceneDocument] = {}

    def _load_index(self) -> list[dict[str, Any]]:
        index_path = self._root / "fixtures.json"
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            entries = payload["fixtures"]
        except (OSError, ValueError, KeyError) as error:
            raise SceneError(
                "import_invalid",
                "Fixture index could not be loaded.",
                "Fix fixtures/fixtures.json.",
                {"error": str(error)},
            ) from error
        if not isinstance(entries, list):
            raise SceneError(
                "import_invalid",
                "Fixture index must contain a 'fixtures' array.",
                "Fix fixtures/fixtures.json.",
            )
        return entries

    def _document(self, fixture_id: str) -> SceneDocument:
        if fixture_id not in self._documents:
            entry = next(
                (candidate for candidate in self._index if candidate.get("id") == fixture_id),
                None,
            )
            if entry is None:
                raise SceneError(
                    "not_found",
                    f"Unknown fixture {fixture_id!r}.",
                    "List fixtures and pick an existing id.",
                )
            file_name = entry.get("file", "")
            if (
                not isinstance(file_name, str)
                or Path(file_name).name != file_name
                or not file_name.endswith(".json")
            ):
                raise SceneError(
                    "import_invalid",
                    f"Fixture {fixture_id!r} has an unsafe file path.",
                    "Use a plain .json file name inside fixtures/scenes/.",
                )
            scene_path = self._root / "scenes" / file_name
            try:
                data = json.loads(scene_path.read_text(encoding="utf-8"))
                document = SceneDocument.model_validate(data)
            except (OSError, ValueError) as error:
                raise SceneError(
                    "import_invalid",
                    f"Fixture {fixture_id!r} could not be parsed.",
                    "Fix the fixture scene JSON.",
                    {"error": str(error)},
                ) from error
            self._documents[fixture_id] = document
        return self._documents[fixture_id]

    def list_fixtures(self) -> list[FixtureSummary]:
        summaries: list[FixtureSummary] = []
        for entry in self._index:
            fixture_id = entry.get("id")
            if not isinstance(fixture_id, str):
                continue
            document = self._document(fixture_id)
            summaries.append(
                FixtureSummary(
                    id=fixture_id,
                    name=document.name,
                    description=entry.get("description", ""),
                    room_count=len(document.rooms),
                    area_m2=sum(polygon_area(room.polygon) for room in document.rooms),
                )
            )
        return summaries

    def create_document(self, fixture_id: str, *, name: str | None = None) -> SceneDocument:
        template = self._document(fixture_id)
        data = template.model_dump(mode="json", by_alias=True)
        document = scene_from_fixture(data)
        if name:
            document = document.model_copy(update={"name": name})
        return document

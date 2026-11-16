"""Typed access to the shared spatial constants in packages/contracts/rules.json."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator

from app.contracts.base import CamelModel
from app.core.paths import project_roots


class Rules(CamelModel):
    """Spatial constants from PRD §6.3.

    Lengths/distances are meters, angles degrees, time budgets milliseconds.
    """

    schema_version: int = Field(alias="schemaVersion")
    wall_height: float = Field(alias="WALL_HEIGHT", gt=0)
    wall_thickness: float = Field(alias="WALL_THICKNESS", gt=0)
    grid_snap: float = Field(alias="GRID_SNAP", gt=0)
    manual_rotation_step: float = Field(alias="MANUAL_ROTATION_STEP", gt=0)
    solver_rotations: list[float] = Field(alias="SOLVER_ROTATIONS", min_length=1)
    door_clear_depth: float = Field(alias="DOOR_CLEAR_DEPTH", ge=0)
    door_approach_width: float = Field(alias="DOOR_APPROACH_WIDTH", ge=0)
    passage_primary: float = Field(alias="PASSAGE_PRIMARY", ge=0)
    passage_secondary: float = Field(alias="PASSAGE_SECONDARY", ge=0)
    window_clear_depth: float = Field(alias="WINDOW_CLEAR_DEPTH", ge=0)
    wall_flush_gap: float = Field(alias="WALL_FLUSH_GAP", ge=0)
    room_inset: float = Field(alias="ROOM_INSET", gt=0)
    min_room_area: float = Field(alias="MIN_ROOM_AREA", gt=0)
    auto_layout_max_items: int = Field(alias="AUTO_LAYOUT_MAX_ITEMS", gt=0)
    solver_time_budget: int = Field(alias="SOLVER_TIME_BUDGET", gt=0)
    solver_max_evals: int = Field(alias="SOLVER_MAX_EVALS", gt=0)
    solver_beam_width: int = Field(alias="SOLVER_BEAM_WIDTH", gt=0)

    @model_validator(mode="after")
    def _check_derived_inset(self) -> Rules:
        expected = self.wall_thickness / 2 + 0.02
        if abs(self.room_inset - expected) > 1e-6:
            raise ValueError(
                f"ROOM_INSET ({self.room_inset}) must equal WALL_THICKNESS / 2 + 0.02 ({expected})"
            )
        return self


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("RULES_PATH")
    if env_path:
        paths.append(Path(env_path))
    paths.extend(root / "packages" / "contracts" / "rules.json" for root in project_roots())
    return paths


def find_rules_path() -> Path:
    candidates = _candidate_paths()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"rules.json not found; searched: {searched}")


def load_rules(path: Path | None = None) -> Rules:
    rules_path = path or find_rules_path()
    return Rules.model_validate_json(rules_path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_rules() -> Rules:
    """Process-wide rules singleton."""
    return load_rules()

"""Load scripted operation cases."""

from __future__ import annotations

import json
from pathlib import Path

from app.contracts.rules import Rules
from app.eval.schema import OperationEvalCase
from app.scene.errors import SceneError

DEFAULT_CASES_PATH = Path("fixtures") / "eval" / "operation-cases.json"


def find_cases_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    from app.core.paths import project_roots

    candidates = [root / DEFAULT_CASES_PATH for root in project_roots()]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"operation cases not found; searched: {candidates}")


def load_cases(path: Path | None = None) -> list[OperationEvalCase]:
    cases_path = find_cases_path(path)
    try:
        payload = json.loads(cases_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SceneError(
            "import_invalid",
            f"Evaluation cases could not be read: {cases_path}",
            "Fix fixtures/eval/operation-cases.json.",
            {"error": str(error)},
        ) from error
    raw_cases = payload["cases"] if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise SceneError(
            "import_invalid",
            "Evaluation cases must be an array or an object with 'cases'.",
            "Fix fixtures/eval/operation-cases.json.",
        )
    cases: list[OperationEvalCase] = []
    for raw in raw_cases:
        try:
            cases.append(OperationEvalCase.model_validate(raw))
        except ValueError as error:
            raise SceneError(
                "import_invalid",
                "An evaluation case does not match the schema.",
                "Check id/scene/operations/expect fields.",
                {"error": str(error)},
            ) from error
    return cases


__all__ = ["load_cases", "find_cases_path", "Rules"]

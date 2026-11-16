"""Deterministic operation runner shared by pytest and the eval report."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.contracts.catalog import CatalogEntry
from app.contracts.patch import PatchOp
from app.contracts.rules import Rules, get_rules
from app.contracts.scene import SceneDocument, Vec2
from app.eval.schema import OperationEvalCase
from app.geometry.collision import (
    item_allows_overlap,
    item_obb,
    item_room_id,
    opening_clearance_obb,
)
from app.geometry.obb import obb_overlap
from app.geometry.polygon import inset_polygon, point_in_polygon
from app.scene.document import validate_document
from app.scene.errors import SceneError
from app.scene.service import SceneService

COLLISION_TOLERANCE = -0.005


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    message: str
    version: int | None = None
    warnings: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_scene(relative_path: str, catalog: dict[str, CatalogEntry] | None = None) -> SceneDocument:
    path = Path(relative_path)
    if not path.is_absolute():
        path = _repo_root() / path
    try:
        document = SceneDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise SceneError(
            "import_invalid",
            f"Evaluation scene could not be loaded: {relative_path}",
            "Fix the scene path and JSON.",
            {"error": str(error)},
        ) from error
    validate_document(document, catalog=catalog)
    return document


def _changed_ids(ops: list[PatchOp]) -> tuple[set[str], set[str]]:
    item_ids: set[str] = set()
    opening_ids: set[str] = set()
    for op in ops:
        if op.op == "item.add":
            item_ids.add(op.item.id)
        elif op.op == "item.update":
            item_ids.add(op.item_id)
        elif op.op == "opening.add":
            opening_ids.add(op.opening.id)
        elif op.op == "opening.update":
            opening_ids.add(op.opening_id)
    return item_ids, opening_ids


def _assert_no_collision(
    document: SceneDocument,
    item_ids: set[str],
    catalog: dict[str, CatalogEntry],
) -> str | None:
    boxes = [(item, item_obb(item, catalog)) for item in document.items]
    by_id = {item.id: box for item, box in boxes}
    for changed_id in item_ids:
        changed = next((item for item in document.items if item.id == changed_id), None)
        if changed is None or item_allows_overlap(changed, catalog):
            continue
        changed_box = by_id[changed.id]
        changed_room = item_room_id(document, changed)
        for other, other_box in boxes:
            if other.id == changed.id or item_allows_overlap(other, catalog):
                continue
            other_room = item_room_id(document, other)
            if changed_room is not None and other_room is not None and changed_room != other_room:
                continue
            if obb_overlap(changed_box, other_box, tolerance=COLLISION_TOLERANCE):
                return f"items {changed.id} and {other.id} collide"
    return None


def _assert_inside_room(
    document: SceneDocument,
    item_ids: set[str],
    catalog: dict[str, CatalogEntry],
    rules: Rules,
) -> str | None:
    if not document.rooms or not item_ids:
        return None
    for item in document.items:
        if item.id not in item_ids:
            continue
        point: Vec2 = (item.transform.position[0], item.transform.position[2])
        room = next(
            (
                candidate
                for candidate in document.rooms
                if point_in_polygon(point, candidate.polygon)
            ),
            None,
        )
        if room is None:
            return f"item {item.id} is outside every room"
        inset = inset_polygon(room.polygon, rules.room_inset)
        if not all(point_in_polygon(corner, inset) for corner in item_obb(item, catalog).corners()):
            return f"item {item.id} crosses the room boundary"
    return None


def _assert_door_clear(
    document: SceneDocument,
    item_ids: set[str],
    catalog: dict[str, CatalogEntry],
    rules: Rules,
) -> str | None:
    rooms = {room.id: room for room in document.rooms}
    for opening in document.openings:
        if opening.type != "door":
            continue
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        zone = opening_clearance_obb(room, opening, rules)
        if zone is None:
            continue
        for item in document.items:
            if item.id not in item_ids or item_allows_overlap(item, catalog):
                continue
            if obb_overlap(item_obb(item, catalog), zone, tolerance=COLLISION_TOLERANCE):
                return f"item {item.id} blocks door {opening.id}"
    return None


def run_case(
    case: OperationEvalCase,
    catalog: dict[str, CatalogEntry],
    rules: Rules | None = None,
) -> EvalResult:
    rules = rules or get_rules()
    try:
        document = load_scene(case.scene, catalog)
        service = SceneService(document, rules=rules, catalog=catalog)
        outcome = service.commit(case.operations, base_version=document.version)
    except SceneError as error:
        if case.expect.refusal == error.code:
            return EvalResult(case.id, True, f"refused with {error.code}")
        return EvalResult(
            case.id,
            False,
            f"expected refusal {case.expect.refusal!r}, got {error.code}: {error.message}",
        )

    if case.expect.refusal is not None:
        return EvalResult(
            case.id,
            False,
            f"expected refusal {case.expect.refusal!r}, but the commit succeeded",
            outcome.document.version,
        )
    if case.expect.version is not None and outcome.document.version != case.expect.version:
        return EvalResult(
            case.id,
            False,
            f"expected version {case.expect.version}, got {outcome.document.version}",
            outcome.document.version,
        )

    counts = Counter(item.kind_id for item in outcome.document.items)
    for expected in case.expect.items:
        if counts[expected.kind_id] != expected.count:
            return EvalResult(
                case.id,
                False,
                f"expected {expected.count} × {expected.kind_id}, got {counts[expected.kind_id]}",
                outcome.document.version,
            )

    item_ids, _opening_ids = _changed_ids(case.operations)
    checks: dict[str, Any] = {
        "noCollision": lambda: _assert_no_collision(outcome.document, item_ids, catalog),
        "insideRoom": lambda: _assert_inside_room(outcome.document, item_ids, catalog, rules),
        "doorClear": lambda: _assert_door_clear(outcome.document, item_ids, catalog, rules),
    }
    for assertion in case.expect.assertions:
        problem = checks[assertion]()
        if problem:
            return EvalResult(case.id, False, f"{assertion}: {problem}", outcome.document.version)

    return EvalResult(
        case.id,
        True,
        "ok",
        outcome.document.version,
        tuple(warning.code for warning in outcome.warnings),
    )


def run_cases(
    cases: list[OperationEvalCase],
    catalog: dict[str, CatalogEntry],
    rules: Rules | None = None,
) -> list[EvalResult]:
    return [run_case(case, catalog, rules) for case in cases]

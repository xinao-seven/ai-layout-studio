"""Deterministic evaluation runner shared by pytest, CLI and reports (M4-05).

One case table drives four runners:

* ``operation``: semantic ``PatchOp`` commits through ``SceneService``;
* ``solver``: ``auto_layout`` output plus ``verify_layout`` assertions;
* ``verify``: structured issue checks against a fixture;
* ``vlm``: scripted screenshot review -> geometry recheck -> local replan.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.capture.service import CaptureImage
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import ItemSource
from app.contracts.layout import AutoLayoutResult
from app.contracts.patch import PatchOp
from app.contracts.rules import Rules, get_rules
from app.contracts.scene import Item, Room, SceneDocument, Vec2
from app.core.config import Settings
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
from app.solver.circulation import room_blocking_item_ids, room_path_blocked
from app.solver.engine import SolveOptions, SolveSpec, solve_layout
from app.solver.service import SolverService
from app.solver.verify import verify_layout
from app.vlm.client import ScriptedVLMClient
from app.vlm.service import VLMService, build_legend

COLLISION_TOLERANCE = -0.005


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    message: str
    version: int | None = None
    warnings: tuple[str, ...] = ()
    metrics: dict[str, Any] | None = None


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
    if case.kind == "solver":
        return run_solver_case(case, catalog, rules)
    if case.kind == "verify":
        return run_verify_case(case, catalog, rules)
    if case.kind == "vlm":
        return asyncio.run(run_vlm_case(case, catalog, rules))
    return run_operation_case(case, catalog, rules)


def run_operation_case(
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


def _apply_placements(document: SceneDocument, result: AutoLayoutResult) -> SceneDocument:
    removed = set(result.removed_item_ids)
    by_id = {placement.item_id: placement for placement in result.placements}
    items = [item for item in document.items if item.id not in removed]
    present = {item.id for item in items}
    for index, item in enumerate(items):
        placement = by_id.get(item.id)
        if placement is None:
            continue
        items[index] = item.model_copy(
            update={
                "transform": placement.transform,
                "constraint": placement.constraint or item.constraint,
            }
        )
    for placement in result.placements:
        if placement.item_id in present:
            continue
        items.append(
            Item(
                id=placement.item_id,
                kind_id=placement.kind_id,
                variant_id=placement.variant_id,
                transform=placement.transform,
                constraint=placement.constraint,
                source=ItemSource.AGENT,
            )
        )
    return document.model_copy(update={"items": items})


def _room_for_case(case: OperationEvalCase, document: SceneDocument) -> Room | None:
    if case.room_index >= len(document.rooms):
        return None
    return document.rooms[case.room_index]


def run_solver_case(
    case: OperationEvalCase,
    catalog: dict[str, CatalogEntry],
    rules: Rules,
) -> EvalResult:
    if case.layout is None:
        return EvalResult(case.id, False, "solver case is missing layout input")
    try:
        document = load_scene(case.scene, catalog)
    except SceneError as error:
        return EvalResult(case.id, False, error.message)
    room = _room_for_case(case, document)
    if room is None:
        return EvalResult(case.id, False, "roomIndex is out of range")
    specs = [
        SolveSpec(
            kind_id=item.kind_id,
            variant_id=item.variant_id,
            required=item.required,
            constraint=item.constraint,
        )
        for item in case.layout.items
    ]
    options = SolveOptions(
        allow_partial=case.layout.options.allow_partial,
        seed=case.layout.options.seed,
        time_budget_ms=case.layout.options.time_budget_ms,
        max_evals=rules.solver_max_evals,
        replace_existing=case.layout.options.replace_existing,
    )
    try:
        result = solve_layout(
            document,
            catalog,
            rules,
            room_id=room.id,
            items=specs,
            options=options,
        )
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
            f"expected refusal {case.expect.refusal!r}, but the solver succeeded",
        )
    if (
        case.expect.placement_count is not None
        and len(result.placements) != case.expect.placement_count
    ):
        return EvalResult(
            case.id,
            False,
            f"expected {case.expect.placement_count} placements, got {len(result.placements)}",
        )
    if case.expect.kinds:
        placed_kinds = {placement.kind_id for placement in result.placements}
        if not set(case.expect.kinds).issubset(placed_kinds):
            return EvalResult(
                case.id,
                False,
                f"missing placement kinds: {sorted(set(case.expect.kinds) - placed_kinds)}",
            )
    if (
        case.expect.removed_min is not None
        and len(result.removed_item_ids) < case.expect.removed_min
    ):
        return EvalResult(
            case.id,
            False,
            f"expected at least {case.expect.removed_min} removed items, "
            f"got {len(result.removed_item_ids)}",
        )
    if case.expect.unplaced_min is not None and len(result.unplaced) < case.expect.unplaced_min:
        return EvalResult(
            case.id,
            False,
            f"expected at least {case.expect.unplaced_min} unplaced, got {len(result.unplaced)}",
        )
    if (
        case.expect.max_elapsed_ms is not None
        and result.stats.elapsed_ms > case.expect.max_elapsed_ms
    ):
        return EvalResult(
            case.id,
            False,
            f"solver took {result.stats.elapsed_ms} ms (limit {case.expect.max_elapsed_ms})",
        )
    if case.expect.facing_south:
        bed = next(
            (placement for placement in result.placements if placement.kind_id.startswith("bed_")),
            None,
        )
        if bed is None:
            return EvalResult(case.id, False, "no bed placement to check facing")
        front = (math.sin(bed.transform.rotation_y), math.cos(bed.transform.rotation_y))
        dot = front[0] * document.north[0] + front[1] * document.north[1]
        if dot >= -0.99:
            return EvalResult(case.id, False, f"bed front does not face south (dot={dot:.3f})")
    scratch = _apply_placements(document, result)
    verification = verify_layout(scratch, catalog, rules, room_id=room.id)
    if case.expect.ok is not None and verification.ok != case.expect.ok:
        return EvalResult(
            case.id,
            False,
            "verify_layout returned "
            + ("ok" if verification.ok else "issues")
            + ": "
            + ", ".join(issue.code for issue in verification.issues),
        )
    codes = {issue.code for issue in verification.issues}
    missing = set(case.expect.issue_codes) - codes
    if missing:
        return EvalResult(case.id, False, f"missing verify issues: {sorted(missing)}")
    return EvalResult(
        case.id,
        True,
        f"ok ({len(result.placements)} placements, {result.stats.elapsed_ms} ms)",
        metrics={
            "solverElapsedMs": result.stats.elapsed_ms,
            "candidates": result.stats.candidates,
            "backtracks": result.stats.backtracks,
            "score": result.stats.score,
        },
    )


def run_verify_case(
    case: OperationEvalCase,
    catalog: dict[str, CatalogEntry],
    rules: Rules,
) -> EvalResult:
    try:
        document = load_scene(case.scene, catalog)
    except SceneError as error:
        return EvalResult(case.id, False, error.message)
    room = _room_for_case(case, document)
    result = verify_layout(
        document,
        catalog,
        rules,
        room_id=room.id if room is not None else None,
    )
    if case.expect.ok is not None and result.ok != case.expect.ok:
        return EvalResult(
            case.id,
            False,
            f"expected ok={case.expect.ok}, got ok={result.ok}",
        )
    codes = {issue.code for issue in result.issues}
    missing = set(case.expect.issue_codes) - codes
    if missing:
        return EvalResult(case.id, False, f"missing issues: {sorted(missing)}")
    return EvalResult(case.id, True, f"ok ({len(result.issues)} issues)")


async def run_vlm_case(
    case: OperationEvalCase,
    catalog: dict[str, CatalogEntry],
    rules: Rules,
) -> EvalResult:
    try:
        document = load_scene(case.scene, catalog)
    except SceneError as error:
        return EvalResult(case.id, False, error.message)
    room = _room_for_case(case, document)
    if room is None:
        return EvalResult(case.id, False, "roomIndex is out of range")
    blockers = room_blocking_item_ids(document, room, catalog, rules)
    legend = build_legend(document, room)
    labels = {entry["itemId"]: entry["label"] for entry in legend["items"] if entry.get("itemId")}
    by_id = {item.id: item for item in document.items}
    plant_labels = [
        labels.get(item_id, item_id)
        for item_id in blockers
        if by_id.get(item_id) is not None and by_id[item_id].kind_id == "plant"
    ]
    script = _substitute(
        case.vlm_script or [],
        {
            "$blockers": [labels.get(item_id, item_id) for item_id in blockers],
            "$plantBlocker": plant_labels[:1],
        },
    )
    client = ScriptedVLMClient(script)
    settings = Settings(environment="test", data_dir=Path("./data"))
    service = VLMService(client, settings)
    solver = SolverService(settings)
    image = Image.new("RGBA", (160, 120), (24, 24, 24, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    capture = CaptureImage(
        png=buffer.getvalue(),
        legend={},
        width=160,
        height=120,
    )
    try:
        reviewed = await service.review_and_replan(
            document,
            room,
            capture,
            catalog,
            rules,
            solver,
        )
    finally:
        await solver.shutdown()
    if (
        case.expect.visual_status is not None
        and reviewed.review.status != case.expect.visual_status
    ):
        return EvalResult(
            case.id,
            False,
            f"expected visual status {case.expect.visual_status!r}, got {reviewed.review.status!r}",
        )
    if (
        case.expect.unconfirmed_min is not None
        and reviewed.review.unconfirmed_count < case.expect.unconfirmed_min
    ):
        return EvalResult(
            case.id,
            False,
            f"expected at least {case.expect.unconfirmed_min} unconfirmed issues, "
            f"got {reviewed.review.unconfirmed_count}",
        )
    if (
        case.expect.unknown_ids_max is not None
        and reviewed.review.unknown_id_count > case.expect.unknown_ids_max
    ):
        return EvalResult(
            case.id,
            False,
            f"expected at most {case.expect.unknown_ids_max} unknown ids, "
            f"got {reviewed.review.unknown_id_count}",
        )
    if case.expect.rechecked is not None and reviewed.review.rechecked != case.expect.rechecked:
        return EvalResult(
            case.id,
            False,
            f"expected rechecked={case.expect.rechecked}, got {reviewed.review.rechecked}",
        )
    scratch = document
    if reviewed.placements and reviewed.review.rechecked:
        scratch = _apply_placements(
            document,
            AutoLayoutResult(
                placements=reviewed.placements,
                unplaced=[],
                removed_item_ids=[],
                stats={"candidates": 0, "backtracks": 0, "elapsed_ms": 0, "score": 0.0},
            ),
        )
        verification = verify_layout(scratch, catalog, rules, room_id=room.id)
        if not verification.ok:
            return EvalResult(
                case.id,
                False,
                "replan still has geometry issues: "
                + ", ".join(issue.code for issue in verification.issues),
            )
    path_blocked = room_path_blocked(scratch, room, catalog, rules)
    if case.expect.path_blocked is not None and path_blocked != case.expect.path_blocked:
        return EvalResult(
            case.id,
            False,
            f"expected pathBlocked={case.expect.path_blocked}, got {path_blocked}",
        )
    return EvalResult(
        case.id,
        True,
        f"visual {reviewed.review.status}, rechecked={reviewed.review.rechecked}",
        metrics={
            "vlmStatus": reviewed.review.status,
            "vlmParseAttempts": reviewed.review.parse_attempts,
            "vlmRounds": reviewed.review.rounds,
            "vlmConfirmed": len(reviewed.review.confirmed),
            "vlmUnconfirmed": reviewed.review.unconfirmed_count,
            "vlmUnknownIds": reviewed.review.unknown_id_count,
        },
    )


def _substitute(value: Any, context: dict[str, list[str]]) -> Any:
    if isinstance(value, str) and value in context:
        return context[value]
    if isinstance(value, dict):
        return {key: _substitute(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, context) for item in value]
    return value


def run_cases(
    cases: list[OperationEvalCase],
    catalog: dict[str, CatalogEntry],
    rules: Rules | None = None,
) -> list[EvalResult]:
    return [run_case(case, catalog, rules) for case in cases]

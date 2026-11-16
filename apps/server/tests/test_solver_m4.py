"""M4 solver, verification and circulation tests (G2 foundations)."""

from __future__ import annotations

import math

import pytest

from app.catalog.service import CatalogService
from app.contracts.enums import FacingDirection
from app.contracts.rules import get_rules
from app.contracts.scene import FacingTowardSpec, Item, ItemConstraint, Room
from app.scene.errors import SceneError
from app.scene.fixtures import FixtureService
from app.solver.circulation import room_blocking_item_ids, room_path_blocked
from app.solver.engine import SolveOptions, SolveSpec, solve_layout
from app.solver.verify import verify_layout


@pytest.fixture(scope="module")
def catalog() -> dict:
    return CatalogService().by_kind


@pytest.fixture(scope="module")
def rules():
    return get_rules()


def _bedroom_specs() -> list[SolveSpec]:
    return [
        SolveSpec(
            "bed_double",
            constraint=ItemConstraint(facing=FacingDirection.SOUTH),
        ),
        SolveSpec("wardrobe"),
        SolveSpec("nightstand"),
    ]


def test_solver_g2_bedroom_deterministic_and_verifies(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    room = document.rooms[0]
    first = solve_layout(
        document,
        catalog,
        rules,
        room_id=room.id,
        items=_bedroom_specs(),
        options=SolveOptions(time_budget_ms=5000),
    )
    second = solve_layout(
        document,
        catalog,
        rules,
        room_id=room.id,
        items=_bedroom_specs(),
        options=SolveOptions(time_budget_ms=5000),
    )
    assert len(first.placements) == 3
    assert first.unplaced == []
    signature = lambda result: [  # noqa: E731
        (
            placement.kind_id,
            tuple(round(value, 4) for value in placement.transform.position),
            round(placement.transform.rotation_y, 4),
        )
        for placement in result.placements
    ]
    assert signature(first) == signature(second)
    assert first.stats.elapsed_ms <= 5000

    bed = next(placement for placement in first.placements if placement.kind_id == "bed_double")
    front = (
        math.sin(bed.transform.rotation_y),
        math.cos(bed.transform.rotation_y),
    )
    assert front[0] * document.north[0] + front[1] * document.north[1] < -0.99

    items = list(document.items)
    for placement in first.placements:
        items.append(
            Item(
                id=placement.item_id,
                kind_id=placement.kind_id,
                variant_id=placement.variant_id,
                transform=placement.transform,
                constraint=placement.constraint,
            )
        )
    built = document.model_copy(update={"items": items})
    result = verify_layout(built, catalog, rules, room_id=room.id)
    assert result.ok, result.model_dump(mode="json", by_alias=True)
    assert result.issues == []


def test_solver_rejects_concave_room(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    room = document.rooms[0].model_copy(
        update={"polygon": [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]}
    )
    document = document.model_copy(update={"rooms": [room]})
    with pytest.raises(SceneError) as error:
        solve_layout(
            document,
            catalog,
            rules,
            room_id=room.id,
            items=[SolveSpec("plant")],
        )
    assert error.value.code == "unsupported_room_shape"


def test_solver_reports_item_too_large(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    document = document.model_copy(
        update={
            "rooms": [
                Room(
                    id="room_tiny",
                    name="Tiny",
                    type="bedroom",
                    polygon=[(0, 0), (1.4, 0), (1.4, 1.4), (0, 1.4)],
                )
            ],
            "openings": [],
        }
    )
    with pytest.raises(SceneError) as error:
        solve_layout(
            document,
            catalog,
            rules,
            room_id="room_tiny",
            items=[SolveSpec("bed_double")],
        )
    assert error.value.code == "item_too_large"


def test_solver_allows_partial_result(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    document = document.model_copy(
        update={
            "rooms": [
                Room(
                    id="room_tight",
                    name="Tight",
                    type="bedroom",
                    polygon=[(0, 0), (2.4, 0), (2.4, 2.6), (0, 2.6)],
                )
            ],
            "openings": [],
            "items": [],
        }
    )
    result = solve_layout(
        document,
        catalog,
        rules,
        room_id="room_tight",
        items=[SolveSpec("bed_double"), SolveSpec("bed_double", required=False)],
        options=SolveOptions(allow_partial=True),
    )
    assert result.placements
    assert any(item.kind_id == "bed_double" for item in result.unplaced)


def test_verify_g3_fixture_passes_geometric_checks(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom-g3")
    room = document.rooms[0]
    result = verify_layout(document, catalog, rules, room_id=room.id)
    assert result.ok
    assert result.issues == []


def test_circulation_recheck_finds_blocking_item(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom-g3")
    room = document.rooms[0]
    assert room_path_blocked(document, room, catalog, rules)
    blockers = room_blocking_item_ids(document, room, catalog, rules)
    assert blockers
    plant = next(item for item in document.items if item.kind_id == "plant")
    assert plant.id in blockers
    trimmed = document.model_copy(
        update={"items": [item for item in document.items if item.id != plant.id]}
    )
    assert not room_path_blocked(trimmed, room, catalog, rules)


def test_facing_toward_window_is_resolved(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    room = document.rooms[0]
    result = solve_layout(
        document,
        catalog,
        rules,
        room_id=room.id,
        items=[
            SolveSpec(
                "chair",
                constraint=ItemConstraint(
                    facing=FacingTowardSpec(toward="window"),
                ),
            )
        ],
    )
    assert result.placements
    assert result.placements[0].transform.rotation_y is not None


def test_optional_item_without_allow_partial(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    document = document.model_copy(
        update={
            "rooms": [
                Room(
                    id="room_tight_optional",
                    name="Tight",
                    type="bedroom",
                    polygon=[(0, 0), (2.4, 0), (2.4, 2.6), (0, 2.6)],
                )
            ],
            "openings": [],
            "items": [],
        }
    )
    result = solve_layout(
        document,
        catalog,
        rules,
        room_id="room_tight_optional",
        items=[SolveSpec("bed_double"), SolveSpec("bed_double", required=False)],
    )
    assert len(result.placements) == 1
    assert [item.kind_id for item in result.unplaced] == ["bed_double"]


def test_duplicate_specs_do_not_report_false_unplaced(catalog, rules) -> None:
    document = FixtureService().create_document("living-room")
    room = document.rooms[0]
    result = solve_layout(
        document,
        catalog,
        rules,
        room_id=room.id,
        items=[SolveSpec("plant"), SolveSpec("plant")],
    )
    assert len(result.placements) == 2
    assert result.unplaced == []


def test_allow_overlap_rug_can_share_bed_footprint(catalog, rules) -> None:
    document = FixtureService().create_document("bedroom")
    document = document.model_copy(
        update={
            "rooms": [
                Room(
                    id="room_rug",
                    name="Rug",
                    type="bedroom",
                    polygon=[(0, 0), (2.6, 0), (2.6, 3.0), (0, 3.0)],
                )
            ],
            "openings": [],
            "items": [],
        }
    )
    result = solve_layout(
        document,
        catalog,
        rules,
        room_id="room_rug",
        items=[SolveSpec("bed_double"), SolveSpec("rug")],
    )
    assert {placement.kind_id for placement in result.placements} == {"bed_double", "rug"}


def test_solver_payload_returns_error_envelope(catalog, rules) -> None:
    from app.eval.runner import load_scene
    from app.solver.engine import solve_layout_payload

    document = load_scene("fixtures/scenes/tiny-room.json", catalog)
    payload = {
        "document": document.model_dump(mode="json", by_alias=True),
        "catalog": [entry.model_dump(mode="json", by_alias=True) for entry in catalog.values()],
        "rules": rules.model_dump(mode="json", by_alias=True),
        "roomId": document.rooms[0].id,
        "items": [{"kindId": "bed_double", "required": True}],
        "options": {},
    }
    response = solve_layout_payload(payload)
    assert response["ok"] is False
    assert response["error"]["code"] == "item_too_large"

"""Regression tests for M2 review findings (reanchor undo, L2, constraints)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.catalog.service import CatalogService
from app.contracts.enums import AnchorType, OpeningType, RoomType
from app.contracts.patch import (
    ItemAddOp,
    ItemUpdateOp,
    OpeningRemoveOp,
    RoomUpdateOp,
)
from app.contracts.scene import (
    Anchor,
    Item,
    ItemConstraint,
    Opening,
    Room,
    SceneDocument,
    Transform,
)
from app.scene.errors import SceneError
from app.scene.factory import empty_scene, scene_from_fixture
from app.scene.service import SceneService

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG = CatalogService().by_kind


def studio_service() -> SceneService:
    payload = json.loads(
        (REPO_ROOT / "fixtures" / "scenes" / "studio.json").read_text(encoding="utf-8")
    )
    document = scene_from_fixture(payload, keep_ids=True)
    return SceneService(document, catalog=CATALOG)


def test_reanchor_removal_undo_redo_round_trip() -> None:
    service = studio_service()
    window_id = "op_studio_bath_window"
    outcome = service.commit(
        [
            RoomUpdateOp(
                op="room.update",
                room_id="room_studio_bath",
                after={"polygon": [[8, 1.6], [10.5, 1.6], [10.5, 3], [8, 3]]},
            )
        ],
        base_version=1,
    )
    assert any(warning.code == "opening_removed" for warning in outcome.warnings)
    assert all(opening.id != window_id for opening in outcome.document.openings)

    undone = service.undo(expected_version=2)
    restored = next(opening for opening in undone.document.openings if opening.id == window_id)
    assert restored.edge_index == 1
    assert restored.offset == pytest.approx(1.5)
    assert undone.document.rooms[1].polygon == [(8, 0), (10.5, 0), (10.5, 3), (8, 3)]

    redone = service.redo(expected_version=3)
    assert all(opening.id != window_id for opening in redone.document.openings)
    assert redone.document.version == 4


def test_reanchor_offset_undo_redo_round_trip() -> None:
    service = studio_service()
    window_id = "op_studio_bath_window"
    outcome = service.commit(
        [
            RoomUpdateOp(
                op="room.update",
                room_id="room_studio_bath",
                after={"polygon": [[8, -0.5], [10.5, -0.5], [10.5, 3], [8, 3]]},
            )
        ],
        base_version=1,
    )
    reanchored = next(opening for opening in outcome.document.openings if opening.id == window_id)
    assert reanchored.offset == pytest.approx(2.0)

    undone = service.undo(expected_version=2)
    restored = next(opening for opening in undone.document.openings if opening.id == window_id)
    assert restored.offset == pytest.approx(1.5)

    redone = service.redo(expected_version=3)
    reanchored_again = next(
        opening for opening in redone.document.openings if opening.id == window_id
    )
    assert reanchored_again.offset == pytest.approx(2.0)


def test_server_rejects_item_crossing_wall_inset() -> None:
    service = studio_service()
    item = Item(
        id="it_inset",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(0.05, 0.0, 1.0), rotation_y=0.0),
    )
    with pytest.raises(SceneError) as error:
        service.commit([ItemAddOp(op="item.add", item=item)], base_version=1)
    assert error.value.code == "out_of_room"


def test_stale_constraint_does_not_bypass_collision() -> None:
    service = studio_service()
    bed = service.document.items[0]
    outcome = service.commit(
        [
            ItemUpdateOp(
                op="item.update",
                item_id=bed.id,
                after={"transform": {"position": [9.5, 0.0, 1.5], "rotationY": 0.0}},
            )
        ],
        base_version=1,
    )
    bathroom_id = next(room.id for room in outcome.document.rooms if room.type == "bathroom")
    moved_bed = next(item for item in outcome.document.items if item.id == bed.id)
    assert moved_bed.constraint is not None
    assert moved_bed.constraint.room_id == bathroom_id
    plant = Item(
        id="it_stale_room",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(9.5, 0.0, 1.5), rotation_y=0.0),
    )
    with pytest.raises(SceneError) as error:
        service.commit([ItemAddOp(op="item.add", item=plant)], base_version=2)
    assert error.value.code == "collision"


def _door_scene() -> SceneDocument:
    document = empty_scene("Door clearance")
    room = Room(
        id="room_door",
        name="Door room",
        type=RoomType.LIVING,
        polygon=[(0, 0), (6, 0), (6, 5), (0, 5)],
    )
    door = Opening(
        id="op_door",
        type=OpeningType.DOOR,
        room_id=room.id,
        edge_index=0,
        offset=3.0,
        width=0.9,
        height=2.1,
    )
    return document.model_copy(update={"rooms": [room], "openings": [door]})


def test_reanchored_door_rechecks_clearance() -> None:
    service = SceneService(_door_scene(), catalog=CATALOG)
    plant = Item(
        id="it_near_door",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(3.0, 0.0, 1.0), rotation_y=0.0),
    )
    service.commit([ItemAddOp(op="item.add", item=plant)], base_version=1)
    with pytest.raises(SceneError) as error:
        service.commit(
            [
                RoomUpdateOp(
                    op="room.update",
                    room_id="room_door",
                    after={"polygon": [[0, 0.4], [6, 0.4], [6, 5], [0, 5]]},
                )
            ],
            base_version=2,
        )
    assert error.value.code == "blocks_door"


def test_direct_opening_remove_clears_item_constraint() -> None:
    service = studio_service()
    item = Item(
        id="it_anchor",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(0.7, 0.0, 3.3), rotation_y=0.0),
        constraint=ItemConstraint(
            room_id="room_studio_main",
            anchor=Anchor(type=AnchorType.DOOR, opening_id="op_studio_entry"),
        ),
    )
    service.commit([ItemAddOp(op="item.add", item=item)], base_version=1)
    outcome = service.commit(
        [OpeningRemoveOp(op="opening.remove", opening_id="op_studio_entry")],
        base_version=2,
    )
    updated = next(candidate for candidate in outcome.document.items if candidate.id == item.id)
    assert updated.constraint is not None
    assert updated.constraint.anchor is not None
    assert updated.constraint.anchor.opening_id is None


def test_no_reanchor_warning_when_opening_is_unaffected() -> None:
    service = studio_service()
    outcome = service.commit(
        [
            RoomUpdateOp(
                op="room.update",
                room_id="room_studio_bath",
                after={"polygon": [[8, 0], [10.5, 0], [10.5, 3], [8, 2.9]]},
            )
        ],
        base_version=1,
    )
    assert not any(warning.code == "opening_reanchored" for warning in outcome.warnings)


def test_room_shrink_rejects_existing_furniture_through_wall() -> None:
    service = studio_service()
    with pytest.raises(SceneError) as error:
        service.commit(
            [
                RoomUpdateOp(
                    op="room.update",
                    room_id="room_studio_main",
                    after={"polygon": [[0, 0], [7, 0], [7, 5], [0, 5]]},
                )
            ],
            base_version=1,
        )
    assert error.value.code == "out_of_room"
    assert "through a wall" in error.value.message

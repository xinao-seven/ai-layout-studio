"""SceneService tests: patches, versions, undo/redo and proposal chains."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.contracts.enums import Actor
from app.contracts.patch import (
    ItemAddOp,
    ItemRemoveOp,
    ItemUpdateOp,
    OpeningAddOp,
    OpeningUpdateOp,
    RoomAddOp,
    RoomRemoveOp,
    RoomUpdateOp,
    SceneUpdateOp,
)
from app.contracts.proposal import Proposal
from app.contracts.scene import (
    Item,
    ItemConstraint,
    Near,
    Opening,
    Room,
    SceneDefaults,
    SceneDocument,
    Transform,
)
from app.scene.errors import SceneError
from app.scene.service import SceneService


def make_document() -> SceneDocument:
    now = datetime.now(UTC)
    return SceneDocument(
        id="scene_test",
        name="Test scene",
        version=1,
        defaults=SceneDefaults(),
        rooms=[
            Room(
                id="room_living",
                name="Living",
                type="living",
                polygon=[(0, 0), (6, 0), (6, 4), (0, 4)],
            )
        ],
        openings=[
            Opening(
                id="op_door",
                type="door",
                room_id="room_living",
                edge_index=0,
                offset=1.2,
                width=0.9,
                height=2.1,
            )
        ],
        items=[],
        created_at=now,
        updated_at=now,
    )


def make_item(
    item_id: str = "it_sofa",
    *,
    kind_id: str = "sofa",
    position: tuple[float, float, float] = (2.0, 0.0, 2.0),
    rotation_y: float = 0.0,
) -> Item:
    return Item(
        id=item_id,
        kind_id=kind_id,
        variant_id="default",
        transform=Transform(position=position, rotation_y=rotation_y),
    )


def add_item(service: SceneService, item: Item, *, base_version: int | None = None):
    version = service.version if base_version is None else base_version
    return service.commit([ItemAddOp(op="item.add", item=item)], base_version=version)


def make_proposal(
    proposal_id: str,
    ops,
    *,
    base_version: int = 1,
    depends_on: list[str] | None = None,
) -> Proposal:
    return Proposal(
        id=proposal_id,
        scene_id="scene_test",
        turn_id="turn_1",
        base_version=base_version,
        depends_on=depends_on or [],
        ops=ops,
        created_at=datetime.now(UTC),
    )


def test_commit_add_item_increments_version_and_is_reversible() -> None:
    service = SceneService(make_document())

    outcome = add_item(service, make_item())

    assert outcome.patch.from_version == 1
    assert outcome.patch.to_version == 2
    assert outcome.patch.actor == Actor.USER
    assert len(outcome.document.items) == 1
    assert outcome.document.version == 2
    assert outcome.inverse_ops[0].op == "item.remove"

    undone = service.undo(expected_version=2)
    assert undone.document.version == 3
    assert undone.document.items == []
    assert service.can_redo

    redone = service.redo(expected_version=3)
    assert redone.document.version == 4
    assert [item.id for item in redone.document.items] == ["it_sofa"]


def test_commit_rejects_stale_base_version() -> None:
    service = SceneService(make_document())

    with pytest.raises(SceneError) as error:
        add_item(service, make_item(), base_version=7)

    assert error.value.code == "version_conflict"
    assert service.version == 1


def test_item_update_partial_transform_and_undo() -> None:
    service = SceneService(make_document())
    add_item(service, make_item())

    outcome = service.commit(
        [
            ItemUpdateOp(
                op="item.update",
                item_id="it_sofa",
                after={"transform": {"position": [4.0, 0.0, 3.0], "rotationY": 1.57}},
            )
        ],
        base_version=2,
    )

    updated = outcome.document.items[0]
    assert updated.transform.position == (4.0, 0.0, 3.0)
    assert updated.transform.rotation_y == pytest.approx(1.57)
    assert outcome.patch.ops[0].before is not None
    assert outcome.patch.ops[0].after is not None

    undone = service.undo(expected_version=3)
    restored = undone.document.items[0]
    assert restored.transform.position == (2.0, 0.0, 2.0)
    assert restored.transform.rotation_y == 0.0


def test_item_update_can_set_constraint() -> None:
    service = SceneService(make_document())
    add_item(service, make_item())

    outcome = service.commit(
        [
            ItemUpdateOp(
                op="item.update",
                item_id="it_sofa",
                after={
                    "constraint": {
                        "roomId": "room_living",
                        "near": {"itemId": "it_sofa", "distance": 1.0},
                    }
                },
            )
        ],
        base_version=2,
    )

    constraint = outcome.document.items[0].constraint
    assert constraint is not None
    assert constraint.room_id == "room_living"
    assert constraint.near is not None
    assert constraint.near.distance == 1.0


def test_item_remove_requires_existing_item() -> None:
    service = SceneService(make_document())

    with pytest.raises(SceneError) as error:
        service.commit([ItemRemoveOp(op="item.remove", item_id="it_missing")], base_version=1)

    assert error.value.code == "not_found"


def test_item_outside_room_is_rejected() -> None:
    service = SceneService(make_document())

    with pytest.raises(SceneError) as error:
        add_item(service, make_item(position=(20.0, 0.0, 20.0)))

    assert error.value.code == "out_of_room"


def test_opening_add_and_overlap_rejected() -> None:
    service = SceneService(make_document())

    outcome = service.commit(
        [
            OpeningAddOp(
                op="opening.add",
                opening=Opening(
                    id="op_window",
                    type="window",
                    room_id="room_living",
                    edge_index=1,
                    offset=1.5,
                    width=1.2,
                    height=1.2,
                    sill_height=0.9,
                ),
            )
        ],
        base_version=1,
    )
    assert len(outcome.document.openings) == 2

    with pytest.raises(SceneError) as error:
        service.commit(
            [
                OpeningAddOp(
                    op="opening.add",
                    opening=Opening(
                        id="op_window2",
                        type="window",
                        room_id="room_living",
                        edge_index=1,
                        offset=1.8,
                        width=1.2,
                        height=1.2,
                        sill_height=0.9,
                    ),
                )
            ],
            base_version=2,
        )
    assert error.value.code == "opening_overlap"


def test_opening_off_wall_rejected() -> None:
    service = SceneService(make_document())

    with pytest.raises(SceneError) as error:
        service.commit(
            [
                OpeningUpdateOp(
                    op="opening.update",
                    opening_id="op_door",
                    after={"width": 2.0, "offset": 0.1},
                )
            ],
            base_version=1,
        )

    assert error.value.code == "opening_off_wall"
    assert service.version == 1


def test_room_add_update_remove() -> None:
    service = SceneService(make_document())
    outcome = service.commit(
        [
            RoomAddOp(
                op="room.add",
                room=Room(
                    id="room_study",
                    name="Study",
                    type="study",
                    polygon=[(6, 0), (9, 0), (9, 4), (6, 4)],
                ),
            )
        ],
        base_version=1,
    )
    assert len(outcome.document.rooms) == 2

    outcome = service.commit(
        [
            RoomUpdateOp(
                op="room.update",
                room_id="room_study",
                after={"name": "Office"},
            )
        ],
        base_version=2,
    )
    assert outcome.document.rooms[1].name == "Office"

    outcome = service.commit([RoomRemoveOp(op="room.remove", room_id="room_study")], base_version=3)
    assert len(outcome.document.rooms) == 1


def test_room_remove_rejects_occupied_room() -> None:
    service = SceneService(make_document())
    add_item(service, make_item())

    with pytest.raises(SceneError) as error:
        service.commit([RoomRemoveOp(op="room.remove", room_id="room_living")], base_version=2)

    assert error.value.code == "invalid_input"


def test_client_mutation_id_is_idempotent() -> None:
    service = SceneService(make_document())
    ops = [ItemAddOp(op="item.add", item=make_item())]

    first = service.commit(ops, base_version=1, client_mutation_id="mut_1")
    second = service.commit(ops, base_version=1, client_mutation_id="mut_1")

    assert first.patch == second.patch
    assert service.version == 2
    assert len(service.document.items) == 1


def test_pending_overlay_and_dependency_confirmation() -> None:
    service = SceneService(make_document())
    p1 = make_proposal("prop_sofa", [ItemAddOp(op="item.add", item=make_item())], base_version=1)
    p2 = make_proposal(
        "prop_table",
        [
            ItemAddOp(
                op="item.add",
                item=make_item(
                    "it_table",
                    kind_id="coffee_table",
                    position=(3.0, 0.0, 3.0),
                ).model_copy(
                    update={
                        "constraint": ItemConstraint(
                            room_id="room_living",
                            near=Near(item_id="it_sofa", distance=0.8),
                        )
                    }
                ),
            )
        ],
        base_version=1,
        depends_on=["prop_sofa"],
    )
    service.add_pending(p1)
    service.add_pending(p2)

    overlay = service.overlay_document()
    assert [item.id for item in overlay.items] == ["it_sofa", "it_table"]
    assert service.version == 1

    first = service.confirm_proposal("prop_sofa")
    assert first.patch.to_version == 2
    assert [item.id for item in service.document.items] == ["it_sofa"]

    second = service.confirm_proposal("prop_table")
    assert second.patch.to_version == 3
    assert [item.id for item in service.document.items] == ["it_sofa", "it_table"]


def test_confirm_rejects_missing_dependency() -> None:
    service = SceneService(make_document())
    proposal = make_proposal(
        "prop_orphan",
        [ItemAddOp(op="item.add", item=make_item())],
        depends_on=["prop_missing"],
    )

    with pytest.raises(SceneError) as error:
        service.add_pending(proposal)

    assert error.value.code == "proposal_expired"


def test_reject_expires_dependents() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_a", [ItemAddOp(op="item.add", item=make_item())]))
    service.add_pending(
        make_proposal(
            "prop_b",
            [
                ItemAddOp(
                    op="item.add",
                    item=make_item("it_table", kind_id="coffee_table", position=(3, 0, 3)),
                )
            ],
            depends_on=["prop_a"],
        )
    )

    expired = service.reject_proposal("prop_a", reason="user_rejected")

    assert expired == ["prop_b"]
    assert service.pending_proposals == []


def test_user_commit_expires_pending_proposals() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_sofa", [ItemAddOp(op="item.add", item=make_item())]))

    add_item(service, make_item("it_table", kind_id="coffee_table", position=(3, 0, 3)))

    assert service.pending_proposals == []


def test_undo_without_history_raises() -> None:
    service = SceneService(make_document())

    with pytest.raises(SceneError) as error:
        service.undo(expected_version=1)

    assert error.value.code == "invalid_input"


def test_confirm_expires_pending_from_other_turns() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_turn1", [ItemAddOp(op="item.add", item=make_item())]))
    service.add_pending(
        Proposal(
            id="prop_turn2",
            scene_id="scene_test",
            turn_id="turn_2",
            base_version=1,
            depends_on=[],
            ops=[
                ItemAddOp(
                    op="item.add",
                    item=make_item("it_table", kind_id="coffee_table", position=(3, 0, 3)),
                )
            ],
            created_at=datetime.now(UTC),
        )
    )

    service.confirm_proposal("prop_turn1")

    assert service.pending_proposals == []


def test_duplicate_confirm_is_idempotent() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_once", [ItemAddOp(op="item.add", item=make_item())]))

    first = service.confirm_proposal("prop_once")
    second = service.confirm_proposal("prop_once")

    assert first.already_confirmed is False
    assert second.already_confirmed is True
    assert second.patch == first.patch
    assert service.version == 2


def test_confirm_expires_independent_same_turn_proposal() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_a", [ItemAddOp(op="item.add", item=make_item())]))
    service.add_pending(
        make_proposal(
            "prop_b",
            [
                ItemAddOp(
                    op="item.add",
                    item=make_item("it_table", kind_id="coffee_table", position=(3, 0, 3)),
                )
            ],
        )
    )

    service.confirm_proposal("prop_a")

    assert service.pending_proposals == []
    assert [item.id for item in service.overlay_document().items] == ["it_sofa"]


def test_add_pending_rejects_confirmed_dependency() -> None:
    service = SceneService(make_document())
    service.add_pending(make_proposal("prop_a", [ItemAddOp(op="item.add", item=make_item())]))
    service.confirm_proposal("prop_a")

    with pytest.raises(SceneError) as error:
        service.add_pending(
            make_proposal(
                "prop_late",
                [
                    ItemAddOp(
                        op="item.add",
                        item=make_item("it_table", kind_id="coffee_table", position=(3, 0, 3)),
                    )
                ],
                depends_on=["prop_a"],
            )
        )

    assert error.value.code == "proposal_expired"


def test_proposal_ttl_expires_stale_pending() -> None:
    service = SceneService(make_document())
    stale = make_proposal("prop_stale", [ItemAddOp(op="item.add", item=make_item())])
    stale = stale.model_copy(update={"created_at": datetime.now(UTC) - timedelta(minutes=16)})
    service.add_pending(stale)

    assert service.expire_due() == ["prop_stale"]
    assert service.pending_proposals == []
    with pytest.raises(SceneError) as error:
        service.confirm_proposal("prop_stale")
    assert error.value.code == "not_found"


def test_undo_keeps_history_when_apply_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SceneService(make_document())
    add_item(service, make_item())
    assert service.can_undo

    def fail_apply(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr("app.scene.service.apply_ops", fail_apply)
    with pytest.raises(RuntimeError):
        service.undo(expected_version=2)

    assert service.can_undo
    assert service.version == 2


def test_scene_update_sets_environment_and_is_reversible() -> None:
    service = SceneService(make_document())

    outcome = service.commit(
        [
            SceneUpdateOp(
                op="scene.update",
                after={"environment": {"latitude": 31.23, "longitude": 121.47}},
            )
        ],
        base_version=1,
    )

    assert outcome.document.environment is not None
    assert outcome.document.environment.latitude == pytest.approx(31.23)
    assert outcome.patch.ops[0].op == "scene.update"

    undone = service.undo(expected_version=2)
    assert undone.document.environment is None


def test_scene_update_normalises_north() -> None:
    service = SceneService(make_document())

    outcome = service.commit(
        [SceneUpdateOp(op="scene.update", after={"north": [0.0, -5.0]})],
        base_version=1,
    )

    assert outcome.document.north == pytest.approx((0.0, -1.0))


def test_scene_update_rejects_out_of_range_latitude() -> None:
    service = SceneService(make_document())

    with pytest.raises(ValidationError):
        SceneUpdateOp(
            op="scene.update",
            after={"environment": {"latitude": 95.0}},
        )

    assert service.version == 1

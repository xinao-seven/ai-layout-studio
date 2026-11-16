"""Solver spike tests (M2-08)."""

from __future__ import annotations

from app.contracts.rules import get_rules
from app.contracts.scene import Opening, Room, SceneDefaults, SceneDocument
from app.geometry.obb import OBB
from app.geometry.polygon import inset_polygon, point_in_polygon
from app.solver.spike import candidate_poses, door_clear_ok, group_feasible


def make_room(width: float = 6.0, depth: float = 5.0) -> Room:
    return Room(
        id="room_spike",
        name="Spike",
        type="living",
        polygon=[(0, 0), (width, 0), (width, depth), (0, depth)],
    )


def make_scene(room: Room, with_door: bool = False) -> SceneDocument:
    openings = []
    if with_door:
        openings.append(
            Opening(
                id="op_spike_door",
                type="door",
                room_id=room.id,
                edge_index=0,
                offset=3.0,
                width=0.9,
                height=2.1,
            )
        )
    return SceneDocument(
        id="scene_spike",
        name="Spike",
        version=1,
        defaults=SceneDefaults(),
        rooms=[room],
        openings=openings,
        items=[],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_candidate_poses_are_deterministic_and_inside() -> None:
    rules = get_rules()
    room = make_room()
    size = (1.811, 0.758)
    first = candidate_poses(room, size, rules)
    second = candidate_poses(room, size, rules)
    assert len(first) > 10
    assert first[:20] == second[:20]

    inset = inset_polygon(room.polygon, rules.room_inset)
    for pose in first[:50]:
        box = OBB(pose.center, size, pose.yaw)
        assert all(point_in_polygon(corner, inset) for corner in box.corners())

    wall_poses = [pose for pose in first if pose.rotation_deg == 0.0]
    assert wall_poses


def test_door_clearance_rejects_pose_in_front_of_door() -> None:
    rules = get_rules()
    room = make_room()
    scene = make_scene(room, with_door=True)
    size = (1.2, 0.6)
    assert door_clear_ok(scene, (5.0, 3.0), size, 0.0, rules)
    assert not door_clear_ok(scene, (3.0, 0.5), size, 0.0, rules)


def test_group_feasible_places_sofa_and_coffee_table() -> None:
    rules = get_rules()
    room = make_room()
    scene = make_scene(room)
    poses = group_feasible(scene, room, [(1.811, 0.758), (1.26, 0.77)], rules)
    assert poses is not None
    assert len(poses) == 2
    first = OBB(poses[0].center, (1.811, 0.758), poses[0].yaw)
    second = OBB(poses[1].center, (1.26, 0.77), poses[1].yaw)
    assert not first.to_polygon().intersects(second.to_polygon())


def test_group_infeasible_for_room_sized_item() -> None:
    rules = get_rules()
    room = make_room(3.0, 3.0)
    scene = make_scene(room)
    assert group_feasible(scene, room, [(5.0, 5.0)], rules) is None

"""M2 solver spike: candidate poses, door clearance and group feasibility.

This is deliberately a small, deterministic prototype used to answer the M4
design questions recorded in ADR-0008. It is not the production solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.contracts.rules import Rules
from app.contracts.scene import Room, SceneDocument, Vec2
from app.geometry.collision import opening_clearance_obb
from app.geometry.obb import OBB, obb_overlap
from app.geometry.polygon import inset_polygon, point_in_polygon

DEFAULT_STEP = 0.5
COLLISION_TOLERANCE = -0.005

Rotation = float  # degrees, matching rules.json SOLVER_ROTATIONS


@dataclass(frozen=True)
class Pose:
    center: Vec2
    rotation_deg: float

    @property
    def yaw(self) -> float:
        return math.radians(self.rotation_deg)


def _box_inside(center: Vec2, size: Vec2, yaw: float, inset: list[Vec2]) -> bool:
    return all(point_in_polygon(corner, inset) for corner in OBB(center, size, yaw).corners())


def _extent_along(size: Vec2, yaw: float, normal: Vec2) -> float:
    axis_x = (math.cos(yaw), -math.sin(yaw))
    axis_z = (math.sin(yaw), math.cos(yaw))
    return abs(size[0] / 2.0 * (axis_x[0] * normal[0] + axis_x[1] * normal[1])) + abs(
        size[1] / 2.0 * (axis_z[0] * normal[0] + axis_z[1] * normal[1])
    )


def _dedupe(poses: list[Pose]) -> list[Pose]:
    seen: set[tuple[float, float, float]] = set()
    result: list[Pose] = []
    for pose in poses:
        key = (round(pose.center[0], 3), round(pose.center[1], 3), round(pose.rotation_deg, 3))
        if key in seen:
            continue
        seen.add(key)
        result.append(pose)
    return sorted(result, key=lambda pose: (pose.rotation_deg, pose.center[0], pose.center[1]))


def candidate_poses(
    room: Room,
    size: Vec2,
    rules: Rules,
    rotations: list[Rotation] | None = None,
    step: float = DEFAULT_STEP,
) -> list[Pose]:
    """Generate wall-flush and grid poses whose OBB fits in the room inset."""
    rotations = rotations or rules.solver_rotations
    inset = inset_polygon(room.polygon, rules.room_inset)
    poses: list[Pose] = []

    # Wall-flush poses: sample each inset edge and push the box inward by its
    # half-extent along the wall normal, leaving WALL_FLUSH_GAP.
    for index, a in enumerate(inset):
        b = inset[(index + 1) % len(inset)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length <= 1e-9:
            continue
        tangent = (dx / length, dz / length)
        normal = (-tangent[1], tangent[0])
        distance = 0.0
        while distance <= length + 1e-9:
            point = (a[0] + tangent[0] * distance, a[1] + tangent[1] * distance)
            for rotation in rotations:
                yaw = math.radians(rotation)
                extent = _extent_along(size, yaw, normal)
                center = (
                    point[0] + normal[0] * (extent + rules.wall_flush_gap),
                    point[1] + normal[1] * (extent + rules.wall_flush_gap),
                )
                if _box_inside(center, size, yaw, inset):
                    poses.append(Pose(center, rotation))
            distance += step

    # Interior grid poses keep large rooms solvable when no wall pose fits.
    xs = [point[0] for point in inset]
    zs = [point[1] for point in inset]
    x = min(xs)
    while x <= max(xs) + 1e-9:
        z = min(zs)
        while z <= max(zs) + 1e-9:
            for rotation in rotations:
                yaw = math.radians(rotation)
                if _box_inside((x, z), size, yaw, inset):
                    poses.append(Pose((x, z), rotation))
            z += step
        x += step

    return _dedupe(poses)


def door_clear_ok(
    document: SceneDocument,
    center: Vec2,
    size: Vec2,
    rotation_deg: float,
    rules: Rules,
) -> bool:
    rooms = {room.id: room for room in document.rooms}
    box = OBB(center, size, math.radians(rotation_deg))
    for opening in document.openings:
        if opening.type != "door":
            continue
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        clearance = opening_clearance_obb(room, opening, rules)
        if clearance is not None and obb_overlap(box, clearance, tolerance=COLLISION_TOLERANCE):
            return False
    return True


def group_feasible(
    document: SceneDocument,
    room: Room,
    sizes: list[Vec2],
    rules: Rules,
    rotations: list[Rotation] | None = None,
    step: float = DEFAULT_STEP,
) -> list[Pose] | None:
    """Greedy feasibility probe for a group of item footprints.

    Items are placed in the caller's order (M4 will use anchor priority). Each
    item takes the first deterministic pose that clears doors and earlier items.
    """
    placed: list[OBB] = []
    poses: list[Pose] = []
    for size in sizes:
        chosen: Pose | None = None
        for pose in candidate_poses(room, size, rules, rotations, step):
            box = OBB(pose.center, size, pose.yaw)
            if not door_clear_ok(document, pose.center, size, pose.rotation_deg, rules):
                continue
            if any(obb_overlap(box, other, tolerance=COLLISION_TOLERANCE) for other in placed):
                continue
            chosen = pose
            break
        if chosen is None:
            return None
        placed.append(OBB(chosen.center, size, chosen.yaw))
        poses.append(chosen)
    return poses

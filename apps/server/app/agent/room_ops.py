"""Operation layer for the ``add_room`` agent tool (ADR-0002 three-layer split).

Pure functions only: no registry, no executor, no I/O. The result shape
mirrors ``PlacementResult`` so the executor can reuse ``_from_placement``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.agent.contracts import AddRoomInput
from app.agent.refusals import make_refusal, refusal_from_error
from app.contracts.ids import IdPrefix, new_id
from app.contracts.patch import PatchOp, RoomAddOp
from app.contracts.proposal import Refusal
from app.contracts.rules import Rules
from app.contracts.scene import Room, SceneDocument, Vec2
from app.geometry.errors import GeometryError
from app.geometry.polygon import ensure_ccw, validate_polygon
from app.scene.errors import SceneError
from app.scene.patch import apply_ops


@dataclass(frozen=True)
class RoomPlanResult:
    """Either a validated ops list or the collected refusals."""

    ops: list[PatchOp] = field(default_factory=list)
    room: Room | None = None
    document_after: SceneDocument | None = None
    refusals: list[Refusal] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.ops) and not self.refusals


def _snap_vertex(value: float, step: float) -> float:
    # Half-up rounding matches the client's Math.round semantics; the second
    # rounding drops float dust (e.g. 12.300000000000001 -> 12.3).
    return round(math.floor(value / step + 0.5) * step, 4)


def _snap_polygon(points: list[Vec2], step: float) -> list[Vec2]:
    return [(_snap_vertex(x, step), _snap_vertex(z, step)) for x, z in points]


def _dedupe_vertices(points: list[Vec2]) -> list[Vec2]:
    """Drop consecutive duplicates (including the wrap-around pair)."""
    result: list[Vec2] = []
    for point in points:
        if result and result[-1] == point:
            continue
        result.append(point)
    if len(result) >= 2 and result[0] == result[-1]:
        result.pop()
    return result


def plan_add_room(
    document: SceneDocument,
    request: AddRoomInput,
    *,
    rules: Rules,
) -> RoomPlanResult:
    """Resolve ``add_room`` into a validated ``room.add`` op.

    Grid-snaps vertices, drops duplicates, normalizes winding, validates the
    polygon (simple + minimum area) and mints the room id. Overlap with
    existing rooms is intentionally not checked: the manual ``room.add`` path
    does not check it either.
    """
    snapped = _dedupe_vertices(_snap_polygon(list(request.polygon), rules.grid_snap))
    if len(snapped) < 3:
        return RoomPlanResult(
            refusals=[
                make_refusal(
                    "invalid_polygon",
                    "After grid snapping the outline has fewer than three distinct vertices.",
                    details={"vertexCount": len(snapped)},
                )
            ]
        )
    points = ensure_ccw(snapped)
    try:
        validate_polygon(points, min_area=rules.min_room_area)
    except GeometryError as error:
        return RoomPlanResult(
            refusals=[refusal_from_error(error.code, error.message, error.suggest, error.details)]
        )
    room = Room(
        id=new_id(IdPrefix.ROOM),
        name=(request.name or "").strip() or f"房间 {len(document.rooms) + 1}",
        type=request.type,
        polygon=points,
    )
    ops: list[PatchOp] = [RoomAddOp(op="room.add", room=room)]
    try:
        after, _ = apply_ops(document, ops, validate=True, rules=rules)
    except SceneError as error:
        return RoomPlanResult(
            refusals=[refusal_from_error(error.code, error.message, error.suggest, error.details)]
        )
    return RoomPlanResult(ops=ops, room=room, document_after=after)

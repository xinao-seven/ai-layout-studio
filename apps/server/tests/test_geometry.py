"""Geometry kernel tests, including the shared cross-language fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.contracts.scene import Opening, Room, SceneDefaults, Vec2
from app.geometry import (
    GeometryError,
    derive_wall_segments,
    ensure_ccw,
    inset_polygon,
    obb_overlap,
    point_in_polygon,
    polygon_area,
    reanchor_openings,
    signed_area,
    validate_polygon,
)
from app.geometry.obb import OBB

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = json.loads(
    (REPO_ROOT / "packages" / "contracts" / "geometry-fixtures.json").read_text(encoding="utf-8")
)


def polygon(polygon_id: str) -> list[Vec2]:
    return [tuple(point) for point in FIXTURES["polygons"][polygon_id]]


def test_signed_area_fixtures() -> None:
    assert len(FIXTURES["signedAreaCases"]) >= 4
    for case in FIXTURES["signedAreaCases"]:
        points = polygon(case["polygonId"])
        assert signed_area(points) == pytest.approx(case["expected"])
        assert (signed_area(points) > 0) is case["ccw"]


def test_point_in_polygon_fixtures() -> None:
    assert len(FIXTURES["pointCases"]) >= 10
    for case in FIXTURES["pointCases"]:
        assert (
            point_in_polygon(tuple(case["point"]), polygon(case["polygonId"])) is case["inside"]
        ), case["id"]


def test_wall_fixtures() -> None:
    assert len(FIXTURES["wallCases"]) >= 6
    for case in FIXTURES["wallCases"]:
        room = Room(
            id="room_test",
            name="Test",
            polygon=polygon(case["polygonId"]),
        )
        segments = derive_wall_segments(room, SceneDefaults())
        segment = segments[case["edgeIndex"]]
        assert segment.length == pytest.approx(case["length"])
        assert segment.normal[0] == pytest.approx(case["normal"][0])
        assert segment.normal[1] == pytest.approx(case["normal"][1])
        assert segment.id == f"room_test:e{case['edgeIndex']}"


def test_obb_fixtures() -> None:
    assert len(FIXTURES["obbCases"]) >= 9
    for case in FIXTURES["obbCases"]:
        a = OBB(
            center=tuple(case["a"]["center"]),
            size=tuple(case["a"]["size"]),
            yaw=case["a"]["yaw"],
        )
        b = OBB(
            center=tuple(case["b"]["center"]),
            size=tuple(case["b"]["size"]),
            yaw=case["b"]["yaw"],
        )
        assert obb_overlap(a, b) is case["overlap"], case["id"]
        assert obb_overlap(b, a) is case["overlap"], f"{case['id']} (symmetric)"


def test_inset_fixtures() -> None:
    assert len(FIXTURES["insetCases"]) >= 3
    for case in FIXTURES["insetCases"]:
        inset = inset_polygon(polygon(case["polygonId"]), case["distance"])
        assert polygon_area(inset) == pytest.approx(case["expectedArea"], abs=1e-3), case["id"]


def test_inset_disappears_in_narrow_room() -> None:
    with pytest.raises(GeometryError) as error:
        inset_polygon([(0, 0), (1, 0), (1, 1), (0, 1)], 0.6)
    assert error.value.code == "room_too_small"


def test_invalid_polygon_rejected() -> None:
    with pytest.raises(GeometryError) as error:
        validate_polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert error.value.code == "invalid_polygon"

    with pytest.raises(GeometryError) as error:
        validate_polygon([(0, 0), (0.2, 0), (0.2, 0.2)], min_area=1.0)
    assert error.value.code == "room_too_small"


def test_ensure_ccw_preserves_vertices() -> None:
    cw = polygon("rect-cw")
    ccw = ensure_ccw(cw)
    assert signed_area(ccw) > 0
    assert set(ccw) == set(cw)


def test_reanchor_moves_opening_to_nearest_edge() -> None:
    old_room = Room(id="room_a", name="Old", polygon=polygon("rect-ccw"))
    new_room = Room(
        id="room_a",
        name="New",
        polygon=[(0, 0), (6, 0), (6, 3), (0, 3)],
    )
    opening = Opening(
        id="op_door",
        type="door",
        room_id="room_a",
        edge_index=0,
        offset=2.0,
        width=0.9,
        height=2.1,
    )
    kept, warnings = reanchor_openings(old_room, new_room, [opening])
    assert len(kept) == 1
    assert kept[0].edge_index == 0
    assert kept[0].offset == pytest.approx(2.0)
    assert warnings[0].code == "opening_reanchored"


def test_reanchor_removes_far_opening() -> None:
    old_room = Room(id="room_a", name="Old", polygon=polygon("rect-ccw"))
    new_room = Room(
        id="room_a",
        name="New",
        polygon=[(20, 20), (24, 20), (24, 23), (20, 23)],
    )
    opening = Opening(
        id="op_door",
        type="door",
        room_id="room_a",
        edge_index=0,
        offset=2.0,
        width=0.9,
        height=2.1,
    )
    kept, warnings = reanchor_openings(old_room, new_room, [opening])
    assert kept == []
    assert warnings[0].code == "opening_removed"


def test_wall_normal_is_unit_length() -> None:
    for case in FIXTURES["wallCases"]:
        normal = case["normal"]
        assert math.hypot(normal[0], normal[1]) == pytest.approx(1.0)


def test_inset_multipolygon_uses_largest_component() -> None:
    bridged = [
        (0, 0),
        (2, 0),
        (2, 0.75),
        (2.5, 0.75),
        (2.5, 0),
        (4.5, 0),
        (4.5, 2),
        (2.5, 2),
        (2.5, 1.25),
        (2, 1.25),
        (2, 2),
        (0, 2),
    ]
    inset = inset_polygon(bridged, 0.4)
    assert polygon_area(inset) == pytest.approx(1.44)

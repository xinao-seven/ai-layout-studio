"""Composed-primitive assets: the long-tail modelling escape hatch."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.llm import ScriptedAdapter, ScriptedStep
from app.catalog.glb import glb_stats
from app.catalog.library import AssetLibrary
from app.core.config import Settings
from app.main import create_app
from app.modelgen.compile import compile_spec
from app.modelgen.specs import PrimitivePart, PrimitivesSpec


def drying_rack() -> PrimitivesSpec:
    """Two uprights, one horizontal bar, two feet — the reference request."""
    return PrimitivesSpec(
        family="primitives",
        name="落地晾衣架",
        category="decor",
        parts=[
            PrimitivePart(size=(0.04, 1.6, 0.04), position=(-0.55, 0.8, 0), material="metal"),
            PrimitivePart(size=(0.04, 1.6, 0.04), position=(0.55, 0.8, 0), material="metal"),
            PrimitivePart(size=(1.14, 0.04, 0.04), position=(0, 1.6, 0), material="metal"),
            PrimitivePart(size=(0.5, 0.03, 0.4), position=(-0.55, 0.015, 0), material="metal"),
            PrimitivePart(size=(0.5, 0.03, 0.4), position=(0.55, 0.015, 0), material="metal"),
        ],
    )


# --------------------------------------------------------------------------
# compilation
# --------------------------------------------------------------------------


def test_drying_rack_compiles_with_measured_footprint(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    compiled = compile_spec(drying_rack(), library)
    stats = glb_stats(compiled.model_path)

    # Feet are 0.5 wide at x = ±0.55, so the footprint spans 1.6 m.
    width, depth, height = compiled.entry.variants[0].size_m
    assert width == pytest.approx(1.6, abs=0.003)
    assert depth == pytest.approx(0.4, abs=0.003)
    assert height == pytest.approx(1.62, abs=0.003)
    assert (stats.size[0], stats.size[2], stats.size[1]) == pytest.approx((width, depth, height))
    assert stats.minimum[1] == pytest.approx(0.0, abs=0.003)
    assert stats.triangles == 60
    assert compiled.entry.category.value == "decor"
    assert compiled.entry.source == "library"
    assert compiled.entry.rules is not None
    assert compiled.entry.rules.must_against_wall is False
    assert compiled.thumbnail_path.is_file()


def test_primitives_are_byte_identical_across_libraries(tmp_path: Path) -> None:
    first = compile_spec(drying_rack(), AssetLibrary(tmp_path / "a"))
    second = compile_spec(drying_rack(), AssetLibrary(tmp_path / "b"))
    assert first.spec_hash == second.spec_hash
    assert first.model_path.read_bytes() == second.model_path.read_bytes()


def test_primitives_are_idempotent(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    first = compile_spec(drying_rack(), library)
    second = compile_spec(drying_rack(), library)
    assert first.created is True
    assert second.created is False
    assert first.entry.kind_id == second.entry.kind_id


def test_primitives_recentre_x_and_z(tmp_path: Path) -> None:
    spec = PrimitivesSpec(
        family="primitives",
        name="偏移置物架",
        category="decor",
        parts=[
            PrimitivePart(size=(0.4, 1.0, 0.4), position=(2.0, 0.5, 3.0)),
        ],
    )
    compiled = compile_spec(spec, AssetLibrary(tmp_path))
    stats = glb_stats(compiled.model_path)
    assert stats.minimum[0] == pytest.approx(-0.2, abs=0.003)
    assert stats.minimum[2] == pytest.approx(-0.2, abs=0.003)
    assert stats.maximum[0] == pytest.approx(0.2, abs=0.003)


# --------------------------------------------------------------------------
# guard rails
# --------------------------------------------------------------------------


def test_parts_below_the_floor_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PrimitivesSpec(
            family="primitives",
            name="bad",
            parts=[PrimitivePart(size=(1, 1, 1), position=(0, 0, 0))],
        )


def test_floating_composition_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PrimitivesSpec(
            family="primitives",
            name="bad",
            parts=[PrimitivePart(size=(1, 1, 1), position=(0, 2, 0))],
        )


def test_too_many_parts_are_rejected() -> None:
    parts = [
        PrimitivePart(size=(0.1, 0.1, 0.1), position=(0, 0.05 + index * 0.1, 0))
        for index in range(25)
    ]
    with pytest.raises(ValidationError):
        PrimitivesSpec(family="primitives", name="tower", parts=parts)


def test_out_of_range_part_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PrimitivePart(size=(0.005, 1, 1), position=(0, 0.5, 0))


def test_oversized_composition_is_rejected(tmp_path: Path) -> None:
    spec = PrimitivesSpec(
        family="primitives",
        name="too wide",
        parts=[
            PrimitivePart(size=(0.4, 1, 0.4), position=(-2.0, 0.5, 0)),
            PrimitivePart(size=(0.4, 1, 0.4), position=(2.0, 0.5, 0)),
        ],
    )
    with pytest.raises(ValueError):
        compile_spec(spec, AssetLibrary(tmp_path))


def test_paper_thin_composition_is_rejected(tmp_path: Path) -> None:
    spec = PrimitivesSpec(
        family="primitives",
        name="panel",
        parts=[PrimitivePart(size=(1.0, 1.0, 0.01), position=(0, 0.5, 0))],
    )
    with pytest.raises(ValueError):
        compile_spec(spec, AssetLibrary(tmp_path))


# --------------------------------------------------------------------------
# agent tool
# --------------------------------------------------------------------------


def _steps() -> list[ScriptedStep]:
    return [
        ScriptedStep(tool_calls=[{"function": {"name": "query_scene", "arguments": {}}}]),
        ScriptedStep(
            tool_calls=[
                {
                    "function": {
                        "name": "model_item",
                        "arguments": {
                            "spec": {
                                "family": "primitives",
                                "name": "落地晾衣架",
                                "category": "decor",
                                "parts": [
                                    {
                                        "size": [0.04, 1.6, 0.04],
                                        "position": [-0.55, 0.8, 0],
                                        "material": "metal",
                                    },
                                    {
                                        "size": [0.04, 1.6, 0.04],
                                        "position": [0.55, 0.8, 0],
                                        "material": "metal",
                                    },
                                    {
                                        "size": [1.14, 0.04, 0.04],
                                        "position": [0, 1.6, 0],
                                        "material": "metal",
                                    },
                                ],
                            },
                            "roomId": "$lastRoomId",
                            "relation": {"type": "center"},
                        },
                    }
                }
            ]
        ),
        ScriptedStep(text="已生成落地晾衣架，等待确认。"),
    ]


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings, llm_adapter=ScriptedAdapter(_steps()))) as instance:
        yield instance


def _run_turn(client: TestClient, scene_id: str, message: str) -> dict[str, Any]:
    response = client.post(f"/api/scenes/{scene_id}/turns", json={"message": message})
    assert response.status_code == 201, response.text
    turn_id = response.json()["turn"]["id"]
    deadline = time.monotonic() + 10
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/scenes/{scene_id}/turns/{turn_id}").json()["turn"]
        if last["status"] in {"done", "failed", "cancelled"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"turn did not finish: {last}")


def test_model_item_composes_primitives_end_to_end(client: TestClient) -> None:
    scene = client.post("/api/scenes", json={"fixtureId": "studio"}).json()
    scene_id = scene["id"]
    turn = _run_turn(client, scene_id, "给我做一个落地晾衣架，两个立柱一根横杆")
    assert turn["status"] == "done", turn
    assert turn["usage"]["refusals"] == [], turn["usage"]

    generated = [
        entry
        for entry in client.get("/api/catalog").json()["entries"]
        if entry.get("source") == "library"
    ]
    assert len(generated) == 1
    entry = generated[0]
    assert entry["kindId"].startswith("lib_primitives_")
    assert entry["name"] == "落地晾衣架"
    assert entry["category"] == "decor"
    # 1.6 m uprights plus half the 4 cm bar above them.
    assert entry["variants"][0]["sizeM"][2] == pytest.approx(1.62, abs=0.01)

    proposals = client.get(
        f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
    ).json()["proposals"]
    assert len(proposals) == 1
    added = [op for op in proposals[0]["ops"] if op["op"] == "item.add"]
    assert added and added[0]["item"]["kindId"] == entry["kindId"]

"""M6 tests: parametric asset library, modelgen and the model_item tool."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.llm import ScriptedAdapter, ScriptedStep
from app.catalog.glb import glb_stats
from app.catalog.library import AssetLibrary
from app.catalog.service import CatalogService, find_catalog_path
from app.contracts.catalog import CatalogEntry, CatalogRules, CatalogVariant
from app.contracts.enums import CatalogCategory
from app.contracts.scene import Clearance
from app.core.config import Settings
from app.main import create_app
from app.modelgen.compile import compile_spec
from app.modelgen.families import SourceAsset
from app.modelgen.specs import (
    BookshelfSpec,
    CounterRunSpec,
    DeskLSpec,
    ScaleSpec,
    TvStandSpec,
    WardrobeSpec,
    spec_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# unit: library
# --------------------------------------------------------------------------


def _entry(kind_id: str, *, source: str = "library") -> CatalogEntry:
    return CatalogEntry(
        kind_id=kind_id,
        name=kind_id,
        variants=[
            CatalogVariant(
                id="default",
                size_m=[1.0, 0.5, 1.0],
                model_path=f"/assets/library/models/{kind_id}.glb",
                thumbnail_path=f"/assets/library/thumbnails/{kind_id}.png",
            )
        ],
        default_variant_id="default",
        source=source,
    )


def test_library_manifest_roundtrip(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    hash8 = "abcd1234"
    library.tmp_model_path(hash8).write_bytes(b"glb")
    library.tmp_thumbnail_path(hash8).write_bytes(b"png")
    from app.catalog.library import AssetRecord

    library.register(
        AssetRecord(
            entry=_entry("lib_bookshelf_abcd1234"),
            generator={"family": "bookshelf", "specHash": hash8, "spec": {"family": "bookshelf"}},
        )
    )
    assert [entry.kind_id for entry in library.entries] == ["lib_bookshelf_abcd1234"]

    reopened = AssetLibrary(tmp_path)
    assert [entry.kind_id for entry in reopened.entries] == ["lib_bookshelf_abcd1234"]
    assert reopened.spec_for("lib_bookshelf_abcd1234") == {"family": "bookshelf"}


def test_library_drops_records_with_missing_files(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    from app.catalog.library import AssetRecord

    library.register(
        AssetRecord(
            entry=_entry("lib_bookshelf_deadbeef"),
            generator={"family": "bookshelf", "specHash": "deadbeef"},
        )
    )
    assert len(library.entries) == 1
    # The temp model was never written, so a reload must not surface the entry.
    reopened = AssetLibrary(tmp_path)
    assert reopened.entries == []


def test_library_promote_moves_files_and_survives_gc(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    hash8 = "cafe0001"
    library.tmp_model_path(hash8).write_bytes(b"glb")
    library.tmp_thumbnail_path(hash8).write_bytes(b"png")
    from app.catalog.library import AssetRecord

    library.register(
        AssetRecord(
            entry=_entry("lib_wardrobe_cafe0001"),
            generator={"family": "wardrobe", "specHash": hash8},
        )
    )
    assert library.promote("lib_wardrobe_cafe0001") is True
    assert library.model_path(hash8).is_file()
    assert not library.tmp_model_path(hash8).exists()

    removed = library.gc_tmp(ttl_hours=0)
    assert removed == []
    assert library.model_path(hash8).is_file()


def test_library_gc_reclaims_unpromoted_temp_files(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    hash8 = "beef0002"
    path = library.tmp_model_path(hash8)
    path.write_bytes(b"glb")
    from app.catalog.library import AssetRecord

    library.register(
        AssetRecord(
            entry=_entry("lib_tv_stand_beef0002"),
            generator={"family": "tv_stand", "specHash": hash8},
        )
    )
    removed = library.gc_tmp(ttl_hours=0)
    assert removed == [f"{hash8}.glb"]
    assert not path.exists()
    assert library.entries == []


# --------------------------------------------------------------------------
# unit: catalog merge
# --------------------------------------------------------------------------


def test_catalog_merges_library_entries(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    hash8 = "1234abcd"
    library.tmp_model_path(hash8).write_bytes(b"glb")
    from app.catalog.library import AssetRecord

    library.register(
        AssetRecord(
            entry=_entry(f"lib_bookshelf_{hash8}"),
            generator={"family": "bookshelf", "specHash": hash8},
        )
    )
    service = CatalogService(library=library)
    assert service.builtin_count == 60
    assert len(service.entries) == 61
    assert service.get(f"lib_bookshelf_{hash8}") is not None
    assert service.get("sofa").source == "builtin"

    # Reload keeps library entries instead of dropping them.
    service.reload()
    assert len(service.entries) == 61
    assert len(service.preview()) == 60
    assert len(service.preview_merged()) == 61


def test_catalog_builtin_wins_kind_collision(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    library.ensure_dirs()
    hash8 = "0000aaaa"
    library.tmp_model_path(hash8).write_bytes(b"glb")
    from app.catalog.library import AssetRecord

    # Deliberately collide with a shipped kind id.
    library.register(
        AssetRecord(
            entry=_entry("sofa"),
            generator={"family": "bookshelf", "specHash": hash8},
        )
    )
    service = CatalogService(library=library)
    assert service.get("sofa").source == "builtin"
    assert len(service.entries) == 60


# --------------------------------------------------------------------------
# unit: modelgen
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        BookshelfSpec(
            family="bookshelf",
            width_m=2.4,
            depth_m=0.35,
            height_m=2.6,
            shelves=5,
            door_count=2,
            glass_doors=True,
        ),
        WardrobeSpec(
            family="wardrobe", width_m=2.0, height_m=2.4, door_count=3, door_type="sliding"
        ),
        TvStandSpec(family="tv_stand", width_m=1.8, height_m=0.5, drawers=2),
        DeskLSpec(family="desk_l", width_m=1.6, return_width_m=0.5, return_length_m=1.2),
        CounterRunSpec(family="counter_run", length_m=2.4, segments=3, sink=True, cooktop=True),
    ],
)
def test_compile_produces_declared_size(tmp_path: Path, spec: Any) -> None:
    library = AssetLibrary(tmp_path)
    compiled = compile_spec(spec, library)
    stats = glb_stats(compiled.model_path)
    declared = compiled.entry.variants[0].size_m
    measured = (stats.size[0], stats.size[2], stats.size[1])
    for axis in range(3):
        assert abs(declared[axis] - measured[axis]) <= 0.003
    assert stats.triangles <= 15_000
    assert abs(stats.minimum[1]) <= 0.003
    assert compiled.thumbnail_path.is_file()
    assert compiled.entry.source == "library"


def test_compile_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    spec = BookshelfSpec(family="bookshelf", width_m=1.4, height_m=2.1)
    first = compile_spec(spec, library)
    second = compile_spec(spec, library)
    assert first.spec_hash == second.spec_hash
    assert first.created is True
    assert second.created is False
    assert first.model_path == second.model_path
    assert spec_hash(spec) == first.spec_hash
    assert first.entry.kind_id == f"lib_bookshelf_{first.spec_hash}"


def test_compile_rejects_degenerate_parameters() -> None:
    # L0 guards the parameter envelope: absurd values never reach the builder.
    with pytest.raises(ValidationError):
        BookshelfSpec(family="bookshelf", width_m=5.0)
    with pytest.raises(ValidationError):
        BookshelfSpec(family="bookshelf", shelves=0)
    with pytest.raises(ValidationError):
        DeskLSpec(family="desk_l", return_length_m=3.0)


def test_mesh_builder_rejects_degenerate_boxes() -> None:
    from app.modelgen.mesh import ModelBuilder

    builder = ModelBuilder()
    with pytest.raises(ValueError):
        builder.add_box("oak", (0.0, 0.0, 0.0), (0.0, 1.0, 1.0))


def _source_entry(kind_id: str, category: CatalogCategory) -> CatalogEntry:
    return CatalogEntry(
        kind_id=kind_id,
        name=kind_id,
        category=category,
        variants=[
            CatalogVariant(
                id="default",
                size_m=[1.0, 0.5, 1.0],
                model_path=f"/assets/models/{kind_id}.glb",
                thumbnail_path=f"/assets/thumbnails/{kind_id}.png",
            )
        ],
        default_variant_id="default",
        rules=CatalogRules(must_against_wall=True),
        clearance=Clearance(front=0.6, sides=0.02, back=0.02),
    )


def test_scale_family_is_linear_and_inherits_metadata(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    source = REPO_ROOT / "assets" / "models" / "wardrobe.glb"
    base = glb_stats(source)
    entry = _source_entry("wardrobe", CatalogCategory.STORAGE)
    compiled = compile_spec(
        ScaleSpec(
            family="scale",
            source_kind_id="wardrobe",
            factor_x=1.2,
            factor_y=1.0,
            factor_z=0.9,
        ),
        library,
        resolve_model_path=lambda kind_id: SourceAsset(path=source, entry=entry),
    )
    scaled = glb_stats(compiled.model_path)
    assert abs(scaled.size[0] - base.size[0] * 1.2) < 0.005
    assert abs(scaled.size[1] - base.size[1] * 1.0) < 0.005
    assert abs(scaled.size[2] - base.size[2] * 0.9) < 0.005
    assert abs(scaled.minimum[1]) < 0.005
    # Placement semantics survive the rescale.
    assert compiled.entry.category.value == "storage"
    assert compiled.entry.rules is not None
    assert compiled.entry.rules.must_against_wall is True
    assert compiled.entry.clearance.front == 0.6


def test_scale_family_refuses_non_scalable_categories(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    source = REPO_ROOT / "assets" / "models" / "sofa.glb"
    entry = _source_entry("sofa", CatalogCategory.SOFA)
    with pytest.raises(ValueError):
        compile_spec(
            ScaleSpec(family="scale", source_kind_id="sofa", factor_x=1.1),
            library,
            resolve_model_path=lambda kind_id: SourceAsset(path=source, entry=entry),
        )


# --------------------------------------------------------------------------
# integration: agent tool
# --------------------------------------------------------------------------


def _bookshelf_turn_steps() -> list[ScriptedStep]:
    return [
        ScriptedStep(tool_calls=[{"function": {"name": "query_scene", "arguments": {}}}]),
        ScriptedStep(
            tool_calls=[
                {
                    "function": {
                        "name": "model_item",
                        "arguments": {
                            "spec": {
                                "family": "bookshelf",
                                "widthM": 2.4,
                                "depthM": 0.35,
                                "heightM": 2.6,
                                "shelves": 5,
                                "doorCount": 2,
                                "glassDoors": True,
                            },
                            "roomId": "$lastRoomId",
                            "relation": {"type": "center"},
                        },
                    }
                }
            ]
        ),
        ScriptedStep(text="已生成 2.4 米通顶书柜，等待确认。"),
    ]


def _make_client(settings: Settings, steps: list[ScriptedStep]) -> TestClient:
    return TestClient(create_app(settings, llm_adapter=ScriptedAdapter(steps)))


def _create_scene(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/scenes", json={"fixtureId": "living-room"})
    assert response.status_code == 201, response.text
    return response.json()


def _run_turn(
    client: TestClient, scene_id: str, message: str, timeout: float = 10.0
) -> dict[str, Any]:
    response = client.post(f"/api/scenes/{scene_id}/turns", json={"message": message})
    assert response.status_code == 201, response.text
    turn_id = response.json()["turn"]["id"]
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/scenes/{scene_id}/turns/{turn_id}").json()["turn"]
        if last["status"] in {"done", "failed", "cancelled"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"turn did not finish: {last}")


def test_model_item_generates_places_and_publishes_catalog(test_settings: Settings) -> None:
    with _make_client(test_settings, _bookshelf_turn_steps()) as client:
        document = _create_scene(client)
        scene_id = document["id"]
        turn = _run_turn(client, scene_id, "加一个 2.4 米宽的通顶书柜")
        assert turn["status"] == "done", turn
        assert turn["usage"]["refusals"] == [], turn["usage"]

        # The generated kind is a real catalog entry.
        catalog = client.get("/api/catalog").json()["entries"]
        generated = [entry for entry in catalog if entry.get("source") == "library"]
        assert len(generated) == 1
        kind_id = generated[0]["kindId"]
        assert kind_id.startswith("lib_bookshelf_")
        assert abs(generated[0]["variants"][0]["sizeM"][0] - 2.4) < 0.003

        # A pending proposal references it.
        proposals = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert len(proposals) == 1
        ops = proposals[0]["ops"]
        added = [op for op in ops if op["op"] == "item.add"]
        assert added and added[0]["item"]["kindId"] == kind_id

        # catalog.updated precedes proposal.created on the shared buffer.
        gateway = client.app.state.gateway
        buffered = list(gateway._buffers[scene_id])  # noqa: SLF001 - ordering assertion
        types = [frame.type for frame in buffered]
        assert "catalog.updated" in types
        assert "proposal.created" in types
        assert types.index("catalog.updated") < types.index("proposal.created")
        catalog_frame = next(frame for frame in buffered if frame.type == "catalog.updated")
        assert catalog_frame.payload["entry"]["kindId"] == kind_id


def test_confirming_generated_item_promotes_asset(test_settings: Settings) -> None:
    with _make_client(test_settings, _bookshelf_turn_steps()) as client:
        document = _create_scene(client)
        scene_id = document["id"]
        turn = _run_turn(client, scene_id, "加一个 2.4 米宽的通顶书柜")
        assert turn["status"] == "done", turn

        proposals = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        proposal_id = proposals[0]["id"]

        with client.websocket_connect(f"/ws?sceneId={scene_id}") as websocket:
            assert websocket.receive_json()["type"] == "scene.snapshot"
            websocket.send_json(
                {
                    "v": 1,
                    "ch": "host",
                    "type": "proposal.confirm",
                    "seq": 0,
                    "ts": 1_763_000_000_000,
                    "payload": {"proposalId": proposal_id},
                }
            )
            frames: list[dict[str, Any]] = []
            for _ in range(20):
                frame = websocket.receive_json()
                frames.append(frame)
                if frame.get("type") == "host.ack":
                    break

        entry = next(
            frame["payload"]["entry"]
            for frame in frames
            if frame.get("type") == "catalog.updated"
            and frame["payload"]["entry"]["variants"][0]["modelPath"].startswith(
                "/assets/library/models/"
            )
        )
        assert entry["variants"][0]["modelPath"].startswith("/assets/library/models/")

        # The asset moved out of the temp namespace and the scene stays valid.
        library_root = Path(test_settings.library_dir)
        assert list((library_root / "models").glob("*.glb"))
        scene = client.get(f"/api/scenes/{scene_id}").json()
        assert scene["items"], scene
        assert scene["version"] >= 2


def test_model_item_refuses_when_generation_disabled(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        llm_model="test-model",
        asset_generation_enabled=False,
        _env_file=None,
    )
    with _make_client(settings, _bookshelf_turn_steps()) as client:
        document = _create_scene(client)
        scene_id = document["id"]
        turn = _run_turn(client, scene_id, "加一个通顶书柜")
        assert turn["status"] == "done", turn
        assert "invalid_input" in turn["usage"]["refusals"]
        proposals = client.get(
            f"/api/scenes/{scene_id}/proposals", params={"status": "pending"}
        ).json()["proposals"]
        assert proposals == []


def test_library_dir_is_not_the_repository_assets_tree(test_settings: Settings) -> None:
    library_root = Path(test_settings.library_dir).resolve()
    repo_assets = (REPO_ROOT / "assets").resolve()
    assert repo_assets not in library_root.parents
    assert library_root != repo_assets
    # The shipped catalog is still exactly the fixture file.
    service = CatalogService()
    assert service.builtin_count == len(
        json.loads(find_catalog_path().read_text("utf-8"))["entries"]
    )


# --------------------------------------------------------------------------
# regressions: lifecycle findings from review
# --------------------------------------------------------------------------


def test_compile_is_byte_identical_across_libraries(tmp_path: Path) -> None:
    spec = WardrobeSpec(family="wardrobe", width_m=1.6, height_m=2.2)
    first = compile_spec(spec, AssetLibrary(tmp_path / "a"))
    second = compile_spec(spec, AssetLibrary(tmp_path / "b"))
    assert first.spec_hash == second.spec_hash
    assert first.model_path.read_bytes() == second.model_path.read_bytes()


def test_gc_protects_kinds_referenced_by_scenes(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    compiled = compile_spec(BookshelfSpec(family="bookshelf", width_m=1.1), library)
    kind_id = compiled.entry.kind_id

    assert library.gc_tmp(ttl_hours=0, protected=frozenset({kind_id})) == []
    assert library.get(kind_id) is not None
    assert compiled.model_path.is_file()

    # Unreferenced temp assets are still reclaimed.
    removed = library.gc_tmp(ttl_hours=0)
    assert compiled.model_path.name in removed
    assert library.get(kind_id) is None


def test_library_load_repairs_promoted_entry_with_stale_tmp_urls(tmp_path: Path) -> None:
    from app.catalog.library import AssetRecord

    library = AssetLibrary(tmp_path)
    compiled = compile_spec(TvStandSpec(family="tv_stand", width_m=1.2), library)
    hash8 = compiled.spec_hash
    record = library.get(compiled.entry.kind_id)
    assert record is not None
    # Simulate a crash after the file move but before the URL rewrite.
    library.ensure_dirs()
    library.model_path(hash8).write_bytes(b"glb")
    library.register(
        AssetRecord(
            entry=record.entry,  # still carries /tmp/ URLs
            generator=record.generator,
            created_at=record.created_at,
            promoted=True,
        )
    )

    reopened = AssetLibrary(tmp_path)
    repaired = reopened.get(compiled.entry.kind_id)
    assert repaired is not None
    assert repaired.promoted is True
    assert repaired.entry.variants[0].model_path.startswith("/assets/library/models/")


def test_library_load_promotes_record_when_only_permanent_file_exists(tmp_path: Path) -> None:

    library = AssetLibrary(tmp_path)
    compiled = compile_spec(CounterRunSpec(family="counter_run", length_m=1.5), library)
    hash8 = compiled.spec_hash
    record = library.get(compiled.entry.kind_id)
    assert record is not None
    library.model_path(hash8).write_bytes(b"glb")
    compiled.model_path.unlink()

    reopened = AssetLibrary(tmp_path)
    repaired = reopened.get(compiled.entry.kind_id)
    assert repaired is not None
    assert repaired.promoted is True
    assert repaired.entry.variants[0].model_path.startswith("/assets/library/models/")


def test_promotion_returns_every_generated_entry(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app.contracts.patch import ItemAddOp
    from app.contracts.proposal import Proposal, ProposalStatus
    from app.contracts.scene import Item, Transform
    from app.modelgen.service import ModelGenService

    library = AssetLibrary(tmp_path)
    service = ModelGenService(library)
    shelf = compile_spec(BookshelfSpec(family="bookshelf", width_m=1.3), library)
    robe = compile_spec(WardrobeSpec(family="wardrobe", width_m=1.7), library)

    def item(compiled: Any, index: int) -> Item:
        return Item(
            id=f"item_{index}",
            kind_id=compiled.entry.kind_id,
            variant_id="default",
            transform=Transform(position=[0.0, 0.0, float(index)], rotation_y=0.0),
        )

    proposal = Proposal(
        id="proposal_test",
        scene_id="scene_test",
        turn_id="turn_test",
        base_version=1,
        ops=[
            ItemAddOp(op="item.add", item=item(shelf, 0)),
            ItemAddOp(op="item.add", item=item(robe, 1)),
        ],
        status=ProposalStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    entries = service.on_proposal_confirmed(proposal)
    assert {entry.kind_id for entry in entries} == {
        shelf.entry.kind_id,
        robe.entry.kind_id,
    }
    assert all(
        entry.variants[0].model_path.startswith("/assets/library/models/") for entry in entries
    )
    assert library.model_path(shelf.spec_hash).is_file()
    assert library.model_path(robe.spec_hash).is_file()
    # Already promoted proposals are no-ops.
    assert service.on_proposal_confirmed(proposal) == []


def test_family_is_required_in_the_tool_schema() -> None:
    from app.agent.registry import TOOL_BY_NAME, _parameter_schema

    spec = TOOL_BY_NAME["model_item"]
    schema = _parameter_schema(spec.input_model)
    variants = schema["properties"]["spec"]["oneOf"]
    assert variants
    for variant in variants:
        assert "family" in variant.get("required", [])


def test_library_manifest_is_not_served(test_settings: Settings) -> None:
    with _make_client(test_settings, _bookshelf_turn_steps()) as client:
        document = _create_scene(client)
        _run_turn(client, document["id"], "加一个 2.4 米宽的通顶书柜")
        # The model and thumbnail namespaces are public; the manifest is not.
        assert client.get("/assets/library/manifest.json").status_code == 404

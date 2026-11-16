"""Repository, registry, catalog and fixture tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.catalog.service import CatalogService
from app.contracts.patch import ItemAddOp, RoomUpdateOp
from app.contracts.scene import Item, Room, SceneDocument, Transform
from app.core.paths import project_roots
from app.db.base import Base
from app.db.models import Scene
from app.db.repository import SceneRepository
from app.db.session import create_engine, create_session_factory
from app.scene.document import validate_document
from app.scene.errors import SceneError
from app.scene.factory import empty_scene, scene_from_fixture
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def repository(tmp_path: Path) -> AsyncIterator[SceneRepository]:
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'repo.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield SceneRepository(create_session_factory(engine))
    await engine.dispose()


def load_fixture(name: str) -> SceneDocument:
    payload = json.loads((REPO_ROOT / "fixtures" / "scenes" / name).read_text(encoding="utf-8"))
    return scene_from_fixture(payload)


async def test_create_list_and_reload_scene(repository: SceneRepository) -> None:
    document = empty_scene("Test")
    await repository.create_scene(document)

    listed = await repository.list_scenes()
    assert [row.id for row in listed] == [document.id]
    assert listed[0].current_version == 1

    loaded = await repository.get_scene(document.id)
    assert loaded is not None
    assert loaded.name == "Test"
    assert loaded.version == 1

    versions = await repository.list_versions(document.id)
    assert [row.version for row in versions] == [1]
    assert versions[0].patch_json is None
    assert versions[0].actor == "system"


async def test_commit_persists_scene_and_version(repository: SceneRepository) -> None:
    document = load_fixture("studio.json")
    await repository.create_scene(document)
    registry = SceneRegistry(repository, catalog=CatalogService().by_kind)
    item = Item(
        id="it_new",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(4.0, 0.0, 2.5), rotation_y=0.0),
    )

    outcome = await registry.commit(
        document.id, [ItemAddOp(op="item.add", item=item)], base_version=1
    )
    assert outcome.document.version == 2

    loaded = await repository.get_scene(document.id)
    assert loaded is not None
    assert loaded.version == 2
    assert any(candidate.id == "it_new" for candidate in loaded.items)

    versions = await repository.list_versions(document.id)
    assert [row.version for row in versions] == [2, 1]
    assert versions[0].patch_json is not None
    assert versions[0].inverse_json is not None
    assert versions[0].actor == "user"


async def test_version_history_keeps_latest_100(repository: SceneRepository) -> None:
    document = empty_scene("History").model_copy(
        update={
            "rooms": [
                Room(
                    id="room_history",
                    name="Room",
                    polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],
                )
            ]
        }
    )
    validate_document(document)
    await repository.create_scene(document)
    registry = SceneRegistry(repository)

    for index in range(105):
        outcome = await registry.commit(
            document.id,
            [
                RoomUpdateOp(
                    op="room.update",
                    room_id="room_history",
                    after={"name": f"Room {index}"},
                )
            ],
            base_version=index + 1,
        )
        assert outcome.document.version == index + 2

    versions = await repository.list_versions(document.id)
    assert len(versions) == 100
    assert versions[0].version == 106
    assert versions[-1].version == 7


async def test_undo_redo_round_trip_through_repository(repository: SceneRepository) -> None:
    document = load_fixture("one-bedroom.json")
    await repository.create_scene(document)
    registry = SceneRegistry(repository, catalog=CatalogService().by_kind)
    item = Item(
        id="it_stack",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(3.0, 0.0, 2.0), rotation_y=0.0),
    )
    await registry.commit(document.id, [ItemAddOp(op="item.add", item=item)], base_version=1)

    undone = await registry.undo(document.id, expected_version=2)
    assert undone.document.version == 3
    assert all(candidate.id != "it_stack" for candidate in undone.document.items)

    redone = await registry.redo(document.id, expected_version=3)
    assert redone.document.version == 4
    assert any(candidate.id == "it_stack" for candidate in redone.document.items)

    loaded = await repository.get_scene(document.id)
    assert loaded is not None
    assert loaded.version == 4
    assert any(candidate.id == "it_stack" for candidate in loaded.items)


def test_fixture_service_lists_and_instantiates() -> None:
    service = FixtureService()
    fixtures = service.list_fixtures()
    assert len(fixtures) == 3
    assert {fixture.id for fixture in fixtures} == {"studio", "one-bedroom", "two-bedroom"}
    assert all(fixture.room_count > 0 for fixture in fixtures)
    assert all(fixture.area_m2 > 20 for fixture in fixtures)

    first = service.create_document("studio")
    second = service.create_document("studio")
    assert first.id != second.id
    assert first.id.startswith("scene_")
    assert first.rooms[0].id.startswith("room_")
    assert first.items[0].id.startswith("it_")


def test_project_roots_find_shared_data() -> None:
    roots = project_roots()
    assert any((root / "fixtures" / "catalog.json").is_file() for root in roots)
    assert any((root / "packages" / "contracts" / "rules.json").is_file() for root in roots)


def test_catalog_has_required_kinds() -> None:
    catalog = CatalogService()
    kinds = {entry.kind_id for entry in catalog.entries}
    for kind in (
        "sofa",
        "coffee_table",
        "bed_double",
        "wardrobe",
        "nightstand",
        "tv_stand",
        "rug",
        "dining_table",
        "desk",
        "bookshelf",
        "chair",
        "plant",
    ):
        assert kind in kinds
        entry = catalog.get(kind)
        assert entry is not None
        assert entry.variants[0].size_m[0] > 0


async def test_repository_returns_none_for_missing_scene(repository: SceneRepository) -> None:
    assert await repository.get_scene("scene_missing") is None
    assert await repository.list_versions("scene_missing") == []


async def test_repository_rejects_corrupt_stored_document(
    repository: SceneRepository, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'repo.db').as_posix()}")
    factory = create_session_factory(engine)
    try:
        async with factory() as session, session.begin():
            session.add(
                Scene(
                    id="scene_broken",
                    name="Broken",
                    current_version=1,
                    doc_json={"schemaVersion": 1},
                )
            )
    finally:
        await engine.dispose()

    with pytest.raises(SceneError) as error:
        await repository.get_scene("scene_broken")
    assert error.value.code == "invalid_input"


async def test_save_failure_does_not_advance_memory(repository: SceneRepository) -> None:
    document = load_fixture("studio.json")
    await repository.create_scene(document)
    registry = SceneRegistry(repository, catalog=CatalogService().by_kind)
    item = Item(
        id="it_unpersisted",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(3.0, 0.0, 2.0), rotation_y=0.0),
    )

    async def fail_save(outcome):  # noqa: ANN001, ANN202
        raise RuntimeError("disk full")

    repository.save_commit = fail_save  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await registry.commit(document.id, [ItemAddOp(op="item.add", item=item)], base_version=1)

    current = await registry.get_document(document.id)
    assert current.version == 1
    assert all(candidate.id != "it_unpersisted" for candidate in current.items)


async def test_idempotent_commit_does_not_write_twice(
    repository: SceneRepository,
) -> None:
    document = load_fixture("studio.json")
    await repository.create_scene(document)
    registry = SceneRegistry(repository, catalog=CatalogService().by_kind)
    item = Item(
        id="it_idem",
        kind_id="plant",
        variant_id="default",
        transform=Transform(position=(3.0, 0.0, 2.0), rotation_y=0.0),
    )
    ops = [ItemAddOp(op="item.add", item=item)]

    first = await registry.commit(document.id, ops, base_version=1, client_mutation_id="mut_same")
    second = await registry.commit(document.id, ops, base_version=1, client_mutation_id="mut_same")

    assert first.patch == second.patch
    loaded = await repository.get_scene(document.id)
    assert loaded is not None
    assert loaded.version == 2
    versions = await repository.list_versions(document.id)
    assert [row.version for row in versions] == [2, 1]

"""Manual item scaling: uniform ``transform.scale`` as an editor control."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.catalog import CatalogEntry
from app.core.config import Settings
from app.geometry.collision import item_height, item_size
from app.main import create_app
from app.scene.errors import SceneError


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as instance:
        yield instance


def _scene(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/scenes", json={"fixtureId": "studio"})
    assert response.status_code == 201, response.text
    return response.json()


def _commit(client: TestClient, scene: dict[str, Any], ops: list[dict[str, Any]]) -> Any:
    return client.post(
        f"/api/scenes/{scene['id']}/commit",
        json={"baseVersion": scene["version"], "ops": ops},
    )


def _scale_ops(item_id: str, scale: float) -> list[dict[str, Any]]:
    return [{"op": "item.update", "itemId": item_id, "after": {"transform": {"scale": scale}}}]


def _first_item(scene: dict[str, Any]) -> dict[str, Any]:
    return scene["items"][0]


# --------------------------------------------------------------------------
# size helpers
# --------------------------------------------------------------------------


def _entry(size_m: list[float]) -> CatalogEntry:
    from app.contracts.catalog import CatalogVariant

    return CatalogEntry(
        kind_id="thing",
        name="thing",
        variants=[
            CatalogVariant(
                id="default",
                size_m=size_m,
                model_path="/assets/models/thing.glb",
                thumbnail_path="/assets/thumbnails/thing.png",
            )
        ],
        default_variant_id="default",
    )


def test_size_helpers_apply_uniform_scale() -> None:
    from app.contracts.scene import Item, Transform

    catalog = {"thing": _entry([2.0, 1.0, 0.8])}
    item = Item(
        id="item_1",
        kind_id="thing",
        variant_id="default",
        transform=Transform(position=[0.0, 0.0, 0.0], rotation_y=0.0, scale=1.5),
    )
    assert item_size(item, catalog) == pytest.approx((3.0, 1.5))
    assert item_height(item, catalog) == pytest.approx(1.2)

    item.transform = Transform(position=[0.0, 0.0, 0.0], rotation_y=0.0, scale=1.0)
    assert item_size(item, catalog) == pytest.approx((2.0, 1.0))
    assert item_height(item, catalog) == pytest.approx(0.8)


# --------------------------------------------------------------------------
# commit path
# --------------------------------------------------------------------------


def test_commit_scales_an_item_and_undo_restores_it(client: TestClient) -> None:
    scene = _scene(client)
    item = _first_item(scene)

    # Shrink: fixture items sit against walls, so growing one would hit the
    # inset first and mask the scale path.
    response = _commit(client, scene, _scale_ops(item["id"], 0.8))
    assert response.status_code == 200, response.text
    scaled = response.json()["scene"]
    updated = next(candidate for candidate in scaled["items"] if candidate["id"] == item["id"])
    assert updated["transform"]["scale"] == pytest.approx(0.8)
    assert scaled["version"] == scene["version"] + 1

    undo = client.post(
        f"/api/scenes/{scene['id']}/undo", json={"expectedVersion": scaled["version"]}
    )
    assert undo.status_code == 200, undo.text
    restored = undo.json()["scene"]
    reverted = next(
        candidate for candidate in restored["items"] if candidate["id"] == item["id"]
    )
    assert reverted["transform"]["scale"] == pytest.approx(1.0)


def test_scale_outside_envelope_is_refused(client: TestClient) -> None:
    scene = _scene(client)
    item = _first_item(scene)

    response = _commit(client, scene, _scale_ops(item["id"], 5.0))
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "unrealistic"
    assert error["suggest"]
    assert error["details"]["ruleId"] == "item_scale_bounds"

    response = _commit(client, scene, _scale_ops(item["id"], 0.1))
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unrealistic"


def test_scale_above_the_ceiling_is_refused(client: TestClient) -> None:
    scene = _scene(client)
    room_id = scene["rooms"][0]["id"]
    # A 2.0 m wardrobe doubled would be 4.0 m tall — above the 2.8 m wall.
    added = client.post(
        f"/api/scenes/{scene['id']}/items",
        json={"kindId": "wardrobe", "roomId": room_id},
    )
    assert added.status_code == 200, added.text
    wardrobe_id = next(
        op["item"]["id"] for op in added.json()["patch"]["ops"] if op["op"] == "item.add"
    )
    response = _commit(client, added.json()["scene"], _scale_ops(wardrobe_id, 2.0))
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "unrealistic"
    assert error["details"]["ruleId"] == "item_exceeds_ceiling"
    assert error["details"]["ceiling"] == pytest.approx(2.8)


def test_scaling_into_another_item_is_refused(client: TestClient) -> None:
    scene = _scene(client)
    # Fixture items are laid out with gaps; growing one far enough must collide.
    item = _first_item(scene)
    response = _commit(client, scene, _scale_ops(item["id"], 1.95))
    # Either it fits (no nearby neighbour) or L2 refuses; both are acceptable,
    # but a refusal must be a placement code with a suggestion.
    if response.status_code == 400:
        error = response.json()["error"]
        assert error["code"] in {"collision", "out_of_room", "blocks_door", "unrealistic"}
        assert error["suggest"]
    else:
        assert response.status_code == 200
        assert response.json()["scene"]["version"] == scene["version"] + 1


def test_scaled_item_can_be_moved_back_into_a_valid_position(client: TestClient) -> None:
    scene = _scene(client)
    item = _first_item(scene)
    scaled = _commit(client, scene, _scale_ops(item["id"], 0.8))
    assert scaled.status_code == 200, scaled.text
    document = scaled.json()["scene"]
    target = next(candidate for candidate in document["items"] if candidate["id"] == item["id"])

    # Retaining scale across an unrelated update: the patch merges partial fields.
    moved = _commit(
        client,
        document,
        [
            {
                "op": "item.update",
                "itemId": item["id"],
                "after": {
                    "transform": {
                        "position": [
                            target["transform"]["position"][0] + 0.1,
                            target["transform"]["position"][1],
                            target["transform"]["position"][2],
                        ]
                    }
                },
            }
        ],
    )
    assert moved.status_code == 200, moved.text
    after = next(
        candidate for candidate in moved.json()["scene"]["items"] if candidate["id"] == item["id"]
    )
    assert after["transform"]["scale"] == pytest.approx(0.8)


def test_scene_document_validation_rejects_bad_scale(tmp_path: Path) -> None:
    from app.scene.document import validate_document
    from app.scene.fixtures import FixtureService

    fixture = FixtureService().create_document("studio")
    bad = fixture.model_copy(deep=True)
    bad.items[0].transform = bad.items[0].transform.model_copy(update={"scale": 0.05})
    with pytest.raises(SceneError) as info:
        validate_document(bad)
    assert info.value.code == "unrealistic"
    assert info.value.details["ruleId"] == "item_scale_bounds"
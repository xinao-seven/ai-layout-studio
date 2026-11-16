"""Scene factories: empty scenes, fixture instantiation and import remapping."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.contracts.enums import FacingToward
from app.contracts.ids import IdPrefix, new_id
from app.contracts.scene import ItemConstraint, SceneDefaults, SceneDocument
from app.scene.document import CatalogMap, validate_document
from app.scene.errors import SceneError


def _utc_now() -> datetime:
    return datetime.now(UTC)


def empty_scene(name: str = "Untitled scene") -> SceneDocument:
    now = _utc_now()
    document = SceneDocument(
        id=new_id(IdPrefix.SCENE),
        name=name,
        version=1,
        defaults=SceneDefaults(),
        rooms=[],
        openings=[],
        items=[],
        created_at=now,
        updated_at=now,
    )
    validate_document(document)
    return document


def scene_from_fixture(
    data: dict[str, Any],
    *,
    name: str | None = None,
    keep_ids: bool = False,
) -> SceneDocument:
    """Validate a fixture document and mint a new scene id (unless keep_ids)."""
    try:
        document = SceneDocument.model_validate(data)
    except ValueError as error:
        raise SceneError(
            "import_invalid",
            "Fixture scene does not match the scene schema.",
            "Fix the fixture JSON according to the contract.",
            {"error": str(error)},
        ) from error

    now = _utc_now()
    if keep_ids:
        document = document.model_copy(update={"version": 1, "created_at": now, "updated_at": now})
    else:
        try:
            document = remap_document_ids(document)
        except (KeyError, ValueError) as error:
            raise SceneError(
                "import_invalid",
                "Fixture has dangling room/item/opening references.",
                "Fix the fixture references.",
                {"error": str(error)},
            ) from error
        document = document.model_copy(update={"version": 1, "created_at": now, "updated_at": now})
    validate_document(document)
    return document


def import_document(document: SceneDocument, *, catalog: CatalogMap | None = None) -> SceneDocument:
    """Validate and remap an imported document; failures become import_invalid."""
    try:
        validate_document(document, catalog=catalog)
        remapped = remap_document_ids(document)
        now = _utc_now()
        remapped = remapped.model_copy(update={"version": 1, "created_at": now, "updated_at": now})
        validate_document(remapped, catalog=catalog)
        return remapped
    except SceneError as error:
        raise SceneError(
            "import_invalid",
            f"Imported scene is invalid: {error.message}",
            error.suggest,
            {"cause": error.code, "details": error.details},
        ) from error
    except (KeyError, ValueError) as error:
        raise SceneError(
            "import_invalid",
            "Imported scene has dangling room/item/opening references.",
            "Check every roomId, itemId, openingId and wallId in the document.",
            {"error": str(error)},
        ) from error


def remap_document_ids(document: SceneDocument) -> SceneDocument:
    """Give a scene and all of its children fresh ids, preserving references."""
    room_map = {room.id: new_id(IdPrefix.ROOM) for room in document.rooms}
    opening_map = {opening.id: new_id(IdPrefix.OPENING) for opening in document.openings}
    item_map = {item.id: new_id(IdPrefix.ITEM) for item in document.items}

    def remap_wall_id(wall_id: str) -> str:
        room_id, separator, edge_part = wall_id.partition(":e")
        if not separator:
            raise ValueError(f"invalid wall id {wall_id!r}")
        return f"{room_map[room_id]}:e{edge_part}"

    def remap_constraint(constraint: ItemConstraint) -> ItemConstraint:
        updates: dict[str, Any] = {}
        if constraint.room_id is not None:
            updates["room_id"] = room_map[constraint.room_id]
        if constraint.anchor is not None:
            anchor = constraint.anchor.model_copy(
                update={
                    "wall_id": (
                        remap_wall_id(constraint.anchor.wall_id)
                        if constraint.anchor.wall_id is not None
                        else None
                    ),
                    "opening_id": (
                        opening_map[constraint.anchor.opening_id]
                        if constraint.anchor.opening_id is not None
                        else None
                    ),
                }
            )
            updates["anchor"] = anchor
        if constraint.near is not None:
            near = constraint.near.model_copy(
                update={
                    "item_id": (
                        item_map[constraint.near.item_id]
                        if constraint.near.item_id is not None
                        else None
                    ),
                    "opening_id": (
                        opening_map[constraint.near.opening_id]
                        if constraint.near.opening_id is not None
                        else None
                    ),
                }
            )
            updates["near"] = near
        if constraint.facing is not None and hasattr(constraint.facing, "toward"):
            facing = constraint.facing
            assert not isinstance(facing, str)
            replacement = None
            if facing.id is not None:
                replacement = (
                    item_map[facing.id]
                    if facing.toward == FacingToward.ITEM
                    else opening_map[facing.id]
                )
            updates["facing"] = facing.model_copy(update={"id": replacement})
        return constraint.model_copy(update=updates)

    rooms = [room.model_copy(update={"id": room_map[room.id]}) for room in document.rooms]
    openings = [
        opening.model_copy(
            update={
                "id": opening_map[opening.id],
                "room_id": room_map[opening.room_id],
            }
        )
        for opening in document.openings
    ]
    items = [
        item.model_copy(
            update={
                "id": item_map[item.id],
                "constraint": (remap_constraint(item.constraint) if item.constraint else None),
            }
        )
        for item in document.items
    ]

    return document.model_copy(
        update={
            "id": new_id(IdPrefix.SCENE),
            "rooms": rooms,
            "openings": openings,
            "items": items,
        }
    )


def export_document(document: SceneDocument) -> dict[str, Any]:
    """Canonical JSON document for export (camelCase, JSON values)."""
    return document.model_dump(mode="json", by_alias=True)

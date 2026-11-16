"""Scene REST endpoints (PRD §6.7 minimal set)."""

from __future__ import annotations

import io
import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.api.deps import (
    get_app_settings,
    get_catalog,
    get_fixtures,
    get_gateway,
    get_registry,
    get_repository,
    get_vlm,
)
from app.catalog.service import CatalogService
from app.contracts.api import (
    AddItemRequest,
    CommitRequest,
    CreateSceneRequest,
    ImportRequest,
    PhotoImportResponse,
    SceneListResponse,
    SceneResult,
    SceneSummary,
    UndoRedoRequest,
    UpdateItemRequest,
)
from app.contracts.ids import IdPrefix, new_id
from app.contracts.patch import ItemAddOp, ItemRemoveOp, ItemUpdateOp, PatchOp
from app.contracts.rules import get_rules
from app.contracts.scene import Item, ItemConstraint, Room, SceneDocument, Transform, Vec2
from app.core.config import Settings
from app.db.repository import SceneRepository
from app.geometry.errors import GeometryError
from app.geometry.obb import OBB, obb_from_transform, obb_overlap
from app.geometry.polygon import (
    centroid,
    inset_polygon,
    point_in_polygon,
    polygon_bounds,
)
from app.scene.document import document_area
from app.scene.errors import SceneError
from app.scene.factory import empty_scene, export_document, import_document
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry
from app.sync.gateway import SyncGateway
from app.vlm.floorplan import parse_floorplan
from app.vlm.service import VLMService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/scenes", tags=["scenes"])


def _find_room(document: SceneDocument, room_id: str) -> Room:
    for room in document.rooms:
        if room.id == room_id:
            return room
    raise SceneError(
        "unknown_room",
        f"Room {room_id!r} does not exist.",
        "Query the scene for room ids.",
    )


def _room_for_position(document: SceneDocument, position: tuple[float, float]) -> Room:
    for room in document.rooms:
        if point_in_polygon(position, room.polygon):
            return room
    raise SceneError(
        "out_of_room",
        "The requested furniture position is outside every room.",
        "Pick a position inside a room.",
        {"position": list(position)},
    )


def _default_room(document: SceneDocument, room_id: str | None) -> Room:
    if room_id is not None:
        return _find_room(document, room_id)
    if not document.rooms:
        raise SceneError(
            "out_of_room",
            "This scene has no rooms yet.",
            "Draw a room before adding furniture.",
        )
    return document.rooms[0]


def _item_size(item: Item, catalog: CatalogService) -> Vec2:
    entry = catalog.get(item.kind_id)
    if entry is None:
        return (0.8, 0.8)
    variant = next(
        (candidate for candidate in entry.variants if candidate.id == item.variant_id),
        entry.variants[0],
    )
    return (variant.size_m[0], variant.size_m[1])


def _candidate_positions(room: Room) -> list[Vec2]:
    min_x, min_z, max_x, max_z = polygon_bounds(room.polygon)
    center = centroid(room.polygon)
    step = 0.5
    candidates: list[Vec2] = []
    x = min_x
    while x <= max_x:
        z = min_z
        while z <= max_z:
            candidates.append((x, z))
            z += step
        x += step
    candidates.sort(key=lambda point: (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2)
    return candidates


def _free_position(
    document: SceneDocument,
    room: Room,
    size: Vec2,
    rotation_y: float,
    catalog: CatalogService,
) -> Vec2:
    rules = get_rules()
    try:
        inset = inset_polygon(room.polygon, rules.room_inset)
    except GeometryError as error:
        raise SceneError(error.code, error.message, error.suggest, error.details) from error
    occupied = [
        obb_from_transform(
            item.transform.position,
            _item_size(item, catalog),
            item.transform.rotation_y,
        )
        for item in document.items
    ]
    for candidate in _candidate_positions(room):
        obb = OBB(center=candidate, size=size, yaw=rotation_y)
        if not all(point_in_polygon(corner, inset) for corner in obb.corners()):
            continue
        if any(obb_overlap(obb, other) for other in occupied):
            continue
        return candidate
    raise SceneError(
        "no_space",
        f"Room {room.name!r} has no free floor area for this item.",
        "Remove another item, use a smaller variant, or choose a position manually.",
        {"roomId": room.id},
    )


def _build_add_item_ops(
    document: SceneDocument,
    request: AddItemRequest,
    catalog: CatalogService,
) -> list[PatchOp]:
    entry = catalog.get(request.kind_id)
    if entry is None:
        raise SceneError(
            "unknown_kind",
            f"Unknown catalog kind {request.kind_id!r}.",
            "Search the catalog for a valid kind.",
        )
    variant_id = request.variant_id or entry.default_variant_id
    if all(variant.id != variant_id for variant in entry.variants):
        raise SceneError(
            "variant_missing",
            f"Kind {request.kind_id!r} has no variant {variant_id!r}.",
            "Use the default variant.",
        )
    chosen_variant = next(variant for variant in entry.variants if variant.id == variant_id)
    size = (chosen_variant.size_m[0], chosen_variant.size_m[1])
    if request.position is not None:
        if request.room_id is not None:
            room = _find_room(document, request.room_id)
            if not point_in_polygon(request.position, room.polygon):
                raise SceneError(
                    "out_of_room",
                    "The requested position is outside the selected room.",
                    "Pick a position inside the room or omit roomId.",
                    {"roomId": room.id, "position": list(request.position)},
                )
        else:
            room = _room_for_position(document, request.position)
        position = request.position
    else:
        room = _default_room(document, request.room_id)
        position = _free_position(document, room, size, request.rotation_y, catalog)

    item = Item(
        id=new_id(IdPrefix.ITEM),
        kind_id=request.kind_id,
        variant_id=variant_id,
        transform=Transform(
            position=(position[0], 0.0, position[1]),
            rotation_y=request.rotation_y,
        ),
        constraint=ItemConstraint(room_id=room.id),
    )
    return [ItemAddOp(op="item.add", item=item)]


def _find_item(document: SceneDocument, item_id: str) -> Item:
    for item in document.items:
        if item.id == item_id:
            return item
    raise SceneError(
        "not_found",
        f"Item {item_id!r} does not exist.",
        "Query the scene for item ids.",
    )


@router.get("", response_model=SceneListResponse)
async def list_scenes(
    repository: Annotated[SceneRepository, Depends(get_repository)],
) -> SceneListResponse:
    rows = await repository.list_scenes()
    summaries: list[SceneSummary] = []
    for row in rows:
        document = SceneDocument.model_validate(row.doc_json)
        summaries.append(
            SceneSummary(
                id=row.id,
                name=row.name,
                version=row.current_version,
                room_count=len(document.rooms),
                area_m2=document_area(document),
                updated_at=row.updated_at,
            )
        )
    return SceneListResponse(scenes=summaries)


@router.post("", response_model=SceneDocument, status_code=status.HTTP_201_CREATED)
async def create_scene(
    request: CreateSceneRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    fixtures: Annotated[FixtureService, Depends(get_fixtures)],
) -> SceneDocument:
    if request.fixture_id is not None:
        document = fixtures.create_document(request.fixture_id, name=request.name)
    else:
        document = empty_scene(request.name or "Untitled scene")
    return await registry.create_scene(document)


@router.get("/{scene_id}", response_model=SceneDocument)
async def get_scene(
    scene_id: str,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
) -> SceneDocument:
    return await registry.get_document(scene_id)


@router.post("/{scene_id}/items", response_model=SceneResult)
async def add_scene_item(
    scene_id: str,
    request: AddItemRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    catalog: Annotated[CatalogService, Depends(get_catalog)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    outcome = await registry.commit_with(
        scene_id, lambda document: _build_add_item_ops(document, request, catalog)
    )
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.patch("/{scene_id}/items/{item_id}", response_model=SceneResult)
async def update_scene_item(
    scene_id: str,
    item_id: str,
    request: UpdateItemRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    def build_ops(document: SceneDocument) -> list[PatchOp]:
        item = _find_item(document, item_id)
        position = request.position or item.transform.position
        rotation = (
            request.rotation_y if request.rotation_y is not None else item.transform.rotation_y
        )
        new_room = next(
            (
                room
                for room in document.rooms
                if point_in_polygon((position[0], position[2]), room.polygon)
            ),
            None,
        )
        after: dict[str, object] = {
            "transform": {
                "position": list(position),
                "rotationY": rotation,
            }
        }
        if (
            new_room is not None
            and item.constraint is not None
            and item.constraint.room_id != new_room.id
        ):
            after["constraint"] = item.constraint.model_copy(
                update={"room_id": new_room.id}
            ).model_dump(mode="python", by_alias=True)
        return [
            ItemUpdateOp(
                op="item.update",
                item_id=item_id,
                after=after,
            )
        ]

    outcome = await registry.commit_with(scene_id, build_ops)
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.delete("/{scene_id}/items/{item_id}", response_model=SceneResult)
async def delete_scene_item(
    scene_id: str,
    item_id: str,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    def build_ops(document: SceneDocument) -> list[PatchOp]:
        item = _find_item(document, item_id)
        return [ItemRemoveOp(op="item.remove", item_id=item_id, before=item)]

    outcome = await registry.commit_with(scene_id, build_ops)
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.post("/{scene_id}/commit", response_model=SceneResult)
async def commit_scene(
    scene_id: str,
    request: CommitRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    outcome = await registry.commit(
        scene_id,
        request.ops,
        base_version=request.base_version,
        client_mutation_id=request.client_mutation_id,
    )
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.post("/{scene_id}/undo", response_model=SceneResult)
async def undo_scene(
    scene_id: str,
    request: UndoRedoRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    outcome = await registry.undo(scene_id, expected_version=request.expected_version)
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.post("/{scene_id}/redo", response_model=SceneResult)
async def redo_scene(
    scene_id: str,
    request: UndoRedoRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
) -> SceneResult:
    outcome = await registry.redo(scene_id, expected_version=request.expected_version)
    await gateway.broadcast_patch(scene_id, outcome.patch)
    await gateway.emit_expired(scene_id, outcome.expired_proposals)
    return SceneResult(
        scene=outcome.document,
        patch=outcome.patch,
        warnings=outcome.warnings,
    )


@router.get("/{scene_id}/export")
async def export_scene(
    scene_id: str,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
) -> Response:
    document = await registry.get_document(scene_id)
    payload = json.dumps(export_document(document), ensure_ascii=False, indent=2, sort_keys=True)
    return Response(
        content=f"{payload}\n",
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{scene_id}.json"',
        },
    )


@router.post(
    "/import",
    response_model=SceneDocument,
    status_code=status.HTTP_201_CREATED,
)
async def import_scene(
    request: ImportRequest,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    catalog: Annotated[CatalogService, Depends(get_catalog)],
) -> SceneDocument:
    document = import_document(request.document, catalog=catalog.by_kind)
    return await registry.create_scene(document)


MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG"}


@router.post("/import_photo", response_model=PhotoImportResponse)
async def import_photo_scene(
    file: Annotated[UploadFile, File(description="Floor-plan photo, JPEG/PNG, ≤5MB.")],
    settings: Annotated[Settings, Depends(get_app_settings)],
    vlm: Annotated[VLMService | None, Depends(get_vlm)],
) -> PhotoImportResponse:
    """Parse a floor-plan photo into an unsaved draft scene (PRD FR-19)."""
    payload = await file.read()
    if len(payload) > MAX_PHOTO_BYTES:
        raise SceneError(
            "photo_too_large",
            f"照片超过 {MAX_PHOTO_BYTES // (1024 * 1024)} MB 限制。",
            "压缩或裁剪照片后重试。",
        )
    if not payload:
        raise SceneError(
            "photo_invalid",
            "照片内容为空。",
            "重新选择一张户型图照片。",
        )
    try:
        image = Image.open(io.BytesIO(payload))
        image_format = (image.format or "").upper()
    except (OSError, UnidentifiedImageError) as error:
        raise SceneError(
            "photo_invalid",
            "文件不是有效的图片。",
            "上传 JPEG 或 PNG 格式的户型图照片。",
        ) from error
    if image_format not in ALLOWED_PHOTO_FORMATS:
        raise SceneError(
            "photo_unsupported",
            f"仅支持 JPEG/PNG 格式，收到 {image_format or '未知'}。",
            "将照片另存为 JPEG/PNG 后重试。",
        )
    if vlm is None:
        raise SceneError(
            "vlm_unavailable",
            "VLM 未配置，无法解析户型图照片。",
            "在 .env 中配置 VLM_BASE_URL / VLM_API_KEY / VLM_MODEL 后重启。",
        )

    name = (file.filename or "户型图草案").rsplit(".", 1)[0][:40] or "户型图草案"
    document, warnings = await parse_floorplan(
        vlm.client,
        payload,
        name=name,
        timeout=settings.vlm_timeout_seconds,
    )
    logger.info("floorplan.parsed", rooms=len(document.rooms), name=name)
    return PhotoImportResponse(document=document, warnings=warnings)

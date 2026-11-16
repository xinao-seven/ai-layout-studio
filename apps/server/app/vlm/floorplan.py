"""Floor-plan photo parsing (PRD FR-19): VLM draft + server-side normalisation.

The VLM returns room polygons in meters; the server snaps them to the 0.1 m
grid, normalises winding, drops rooms that would fail scene validation and
builds an unsaved draft document the user can correct before importing.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from app.contracts.enums import RoomType
from app.contracts.ids import IdPrefix, new_id
from app.contracts.rules import get_rules
from app.contracts.scene import Room, SceneDefaults, SceneDocument
from app.scene.errors import SceneError
from app.vlm.client import VLMClient

logger = structlog.get_logger(__name__)

GRID = 0.1
MAX_POINTS_PER_ROOM = 16
MAX_ROOMS = 8

PROMPT_TEMPLATE = """You are a floor-plan parser. The image is a top-down floor-plan \
photo/screenshot of one apartment. Return STRICT JSON only (no markdown, no prose):

{{"rooms": [{{"name": "客厅", "type": "living", "polygon": [[0.0,0.0],[4.2,0.0],[4.2,3.6],[0.0,3.6]]}}]}}

Rules:
- `type` must be one of: living, bedroom, kitchen, bathroom, study, balcony, other.
- `polygon` is the wall-centerline outline in METERS, counter-clockwise, 3-{max_points} \
[x, z] vertex pairs, orthogonal corners preferred.
- Use a sensible absolute scale: living rooms ~4-6 m wide, bedrooms ~3-4 m; wall thickness \
is drawn separately and NOT part of the polygon.
- Do not invent rooms you cannot see; skip labels you cannot read (name "房间").
- Output rooms only; doors/windows are added later by the user."""


def _snap(value: float) -> float:
    return round(value / GRID) * GRID


def _normalise_room_type(raw: Any) -> RoomType:
    try:
        return RoomType(str(raw).strip().lower())
    except ValueError:
        return RoomType.OTHER


def _polygon_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for index, (x1, z1) in enumerate(points):
        x2, z2 = points[(index + 1) % len(points)]
        total += x1 * z2 - x2 * z1
    return total / 2


def _is_simple(points: list[tuple[float, float]]) -> bool:
    def segments_cross(
        a1: tuple[float, float],
        a2: tuple[float, float],
        b1: tuple[float, float],
        b2: tuple[float, float],
    ) -> bool:
        d1 = (a2[0] - a1[0], a2[1] - a1[1])
        d2 = (b2[0] - b1[0], b2[1] - b1[1])
        denominator = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(denominator) <= 1e-9:
            return False
        offset = (b1[0] - a1[0], b1[1] - a1[1])
        t = (offset[0] * d2[1] - offset[1] * d2[0]) / denominator
        u = (offset[0] * d1[1] - offset[1] * d1[0]) / denominator
        return 1e-9 < t < 1 - 1e-9 and 1e-9 < u < 1 - 1e-9

    count = len(points)
    for first in range(count):
        a1 = points[first]
        a2 = points[(first + 1) % count]
        for second in range(first + 1, count):
            if abs(first - second) <= 1 or (first == 0 and second == count - 1):
                continue
            b1 = points[second]
            b2 = points[(second + 1) % count]
            if segments_cross(a1, a2, b1, b2):
                return False
    return True


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise SceneError(
            "parse_failed",
            "VLM 返回的内容里没有 JSON 对象。",
            "重试一次；连续失败请更换更清晰的照片。",
        )
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise SceneError(
            "parse_failed",
            f"VLM 返回的 JSON 无法解析：{error.msg}。",
            "重试一次；连续失败请更换更清晰的照片。",
        ) from error
    if not isinstance(payload, dict):
        raise SceneError(
            "parse_failed",
            "VLM 返回的 JSON 不是对象。",
            "重试一次；连续失败请更换更清晰的照片。",
        )
    return payload


def _coerce_polygon(raw: Any, index: int) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise SceneError(
            "parse_failed",
            f"第 {index + 1} 个房间的 polygon 不是 3 点以上的数组。",
            "重试一次；连续失败请更换更清晰的照片。",
        )
    points: list[tuple[float, float]] = []
    for vertex in raw[:MAX_POINTS_PER_ROOM]:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            raise SceneError(
                "parse_failed",
                f"第 {index + 1} 个房间的顶点不是 [x, z] 数组。",
                "重试一次；连续失败请更换更清晰的照片。",
            )
        x, z = vertex
        if not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
            raise SceneError(
                "parse_failed",
                f"第 {index + 1} 个房间的顶点坐标不是数字。",
                "重试一次；连续失败请更换更清晰的照片。",
            )
        points.append((_snap(float(x)), _snap(float(z))))
    return points


def draft_from_vlm(payload: dict[str, Any], *, name: str) -> tuple[SceneDocument, list[str]]:
    """Normalise a VLM JSON payload into a draft scene document (not saved)."""
    warnings: list[str] = []
    min_area = get_rules().min_room_area
    raw_rooms = payload.get("rooms")
    if not isinstance(raw_rooms, list) or not raw_rooms:
        raise SceneError(
            "parse_failed",
            "VLM 没有识别出任何房间。",
            "重试一次，或改用手绘房间。",
        )

    rooms: list[Room] = []
    for index, raw_room in enumerate(raw_rooms[:MAX_ROOMS]):
        if not isinstance(raw_room, dict):
            warnings.append(f"第 {index + 1} 个房间格式异常，已跳过。")
            continue
        raw_name = str(raw_room.get("name") or f"房间 {index + 1}").strip()[:20]
        try:
            points = _coerce_polygon(raw_room.get("polygon"), index)
        except SceneError as error:
            logger.warning("floorplan.room_skipped", room_index=index, reason=error.message)
            warnings.append(f"第 {index + 1} 个房间（{raw_name}）多边形无效，已跳过。")
            continue
        if not _is_simple(points):
            warnings.append(f"「{raw_name}」轮廓自相交，已跳过。")
            continue
        if abs(_polygon_area(points)) < min_area:
            warnings.append(f"「{raw_name}」面积太小（< {min_area} ㎡），已跳过。")
            continue
        if _polygon_area(points) < 0:
            points.reverse()
        rooms.append(
            Room(
                id=new_id(IdPrefix.ROOM),
                name=raw_name or f"房间 {index + 1}",
                type=_normalise_room_type(raw_room.get("type")),
                polygon=points,
            )
        )

    if not rooms:
        raise SceneError(
            "parse_failed",
            "所有识别出的房间都无效。",
            "重试一次，或改用手绘房间。",
        )
    if len(rooms) < len(raw_rooms[:MAX_ROOMS]):
        warnings.append(f"保留了 {len(rooms)} 个房间；其余可在编辑器里手绘。")

    now = datetime.now(UTC)
    document = SceneDocument(
        id=new_id(IdPrefix.SCENE),
        name=name,
        version=1,
        environment=None,
        defaults=SceneDefaults(),
        rooms=rooms,
        openings=[],
        items=[],
        created_at=now,
        updated_at=now,
    )
    return document, warnings


async def parse_floorplan(
    client: VLMClient,
    png: bytes,
    *,
    name: str,
    timeout: float,
) -> tuple[SceneDocument, list[str]]:
    prompt = PROMPT_TEMPLATE.format(max_points=MAX_POINTS_PER_ROOM)
    text = await client.review(png, prompt, timeout=timeout)
    payload = _extract_json(text)
    return draft_from_vlm(payload, name=name)

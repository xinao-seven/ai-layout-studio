"""Server-side geometry recheck for VLM issues (PRD §6.6).

The VLM only proposes semantic issues (type + labelled ids). This module decides
whether the issue is reproducible with local geometry; unconfirmed issues are
counted as false positives and never trigger a re-layout.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.contracts.catalog import CatalogEntry
from app.contracts.rules import Rules
from app.contracts.scene import Item, Room, SceneDocument
from app.geometry.collision import (
    catalog_items_grouped,
    is_circulation_blocker,
    item_allows_overlap,
    item_height,
    item_obb,
    item_room_id,
    opening_clearance_obb,
    window_clearance_obb,
)
from app.geometry.obb import obb_overlap
from app.solver.circulation import circulation_issue_confirmed

COLLISION_TOLERANCE = -0.005


def _items(document: SceneDocument, item_ids: list[str]) -> list[Item]:
    wanted = set(item_ids)
    return [item for item in document.items if item.id in wanted]


def _room_items(document: SceneDocument, room: Room) -> list[Item]:
    return [item for item in document.items if item_room_id(document, item) == room.id]


def _door_ids(document: SceneDocument, room: Room, opening_ids: list[str]) -> list[str]:
    if opening_ids:
        return opening_ids
    return [
        opening.id
        for opening in document.openings
        if opening.room_id == room.id and opening.type == "door"
    ]


def issue_confirmed(
    document: SceneDocument,
    room: Room,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
    *,
    issue_type: str,
    item_ids: list[str],
    opening_ids: list[str],
) -> bool:
    items = _items(document, item_ids)
    if not items:
        return False
    if issue_type == "blocks_door":
        for opening_id in _door_ids(document, room, opening_ids):
            opening = next(
                (candidate for candidate in document.openings if candidate.id == opening_id),
                None,
            )
            if opening is None or opening.room_id != room.id or opening.type != "door":
                continue
            zone = opening_clearance_obb(room, opening, rules)
            if zone is None:
                continue
            for item in items:
                if item_allows_overlap(item, catalog):
                    continue
                if obb_overlap(item_obb(item, catalog), zone, tolerance=COLLISION_TOLERANCE):
                    return True
        return False

    if issue_type == "window_blocked":
        for opening_id in opening_ids:
            opening = next(
                (candidate for candidate in document.openings if candidate.id == opening_id),
                None,
            )
            if opening is None or opening.room_id != room.id or opening.type != "window":
                continue
            zone = window_clearance_obb(room, opening, rules)
            if zone is None:
                continue
            sill = opening.sill_height if opening.sill_height is not None else 0.9
            for item in items:
                if item_allows_overlap(item, catalog) or item_height(item, catalog) <= sill:
                    continue
                if obb_overlap(item_obb(item, catalog), zone, tolerance=COLLISION_TOLERANCE):
                    return True
        return False

    if issue_type == "walkway_narrow":
        if len(items) >= 2:
            first, second = items[0], items[1]
            if not (
                is_circulation_blocker(first, catalog) and is_circulation_blocker(second, catalog)
            ):
                return False
            if catalog_items_grouped(first, second, catalog):
                return False
            gap = float(
                item_obb(first, catalog)
                .to_polygon()
                .distance(item_obb(second, catalog).to_polygon())
            )
            return 1e-6 < gap < rules.passage_secondary - 1e-6
        target = items[0]
        if not is_circulation_blocker(target, catalog):
            return False
        for other in _room_items(document, room):
            if other.id == target.id or item_allows_overlap(other, catalog):
                continue
            if not is_circulation_blocker(other, catalog):
                continue
            if catalog_items_grouped(target, other, catalog):
                continue
            gap = float(
                item_obb(target, catalog)
                .to_polygon()
                .distance(item_obb(other, catalog).to_polygon())
            )
            if 1e-6 < gap < rules.passage_secondary - 1e-6:
                return True
        return False

    if issue_type == "group_misaligned":
        if len(items) < 2:
            return False
        group = items[0].constraint.group if items[0].constraint is not None else None
        if group is None:
            if not catalog_items_grouped(items[0], items[1], catalog):
                return False
        else:
            for other in items[1:]:
                other_group = other.constraint.group if other.constraint is not None else None
                if other_group != group:
                    return False
        gap = min(
            float(
                item_obb(first, catalog)
                .to_polygon()
                .distance(item_obb(second, catalog).to_polygon())
            )
            for index, first in enumerate(items)
            for second in items[index + 1 :]
        )
        return gap < 0.2 or gap > 1.2

    if issue_type == "circulation_blocked":
        return circulation_issue_confirmed(document, room, catalog, rules, item_ids)

    return False


__all__ = ["issue_confirmed"]

"""Structured layout verification (PRD §6.5 ``verify_layout``, M4-02).

This module is the read-only counterpart to manual placement validation. It
returns *every* issue instead of raising the first one, so the agent and the VLM
loop can reason about a layout without mutating it.

Severity policy: L2 geometry violations are ``error``; L3 design heuristics are
``warning``. ``ok`` is true when no error remains.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.agent.rules import check_item_rules
from app.contracts.catalog import CatalogEntry
from app.contracts.layout import LayoutIssue, VerifyLayoutResult
from app.contracts.rules import Rules
from app.contracts.scene import Item, SceneDocument
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
from app.geometry.polygon import inset_polygon, point_in_polygon
from app.scene.errors import SceneError

COLLISION_TOLERANCE = -0.005


def _item_list(document: SceneDocument, room_id: str | None) -> list[Item]:
    if room_id is None:
        return list(document.items)
    return [item for item in document.items if item_room_id(document, item) == room_id]


def verify_layout(
    document: SceneDocument,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
    *,
    room_id: str | None = None,
) -> VerifyLayoutResult:
    """Return structured issues for a whole scene or a single room."""
    if room_id is not None and all(room.id != room_id for room in document.rooms):
        raise SceneError(
            "unknown_room",
            f"Room {room_id!r} does not exist.",
            "Call query_scene to get the current room ids.",
            {"roomId": room_id},
        )
    rooms = {room.id: room for room in document.rooms}
    selected = _item_list(document, room_id)
    issues: list[LayoutIssue] = []

    for item in selected:
        containing = item_room_id(document, item)
        room = rooms.get(containing) if containing is not None else None
        if room is None:
            issues.append(
                LayoutIssue(
                    code="out_of_room",
                    severity="error",
                    message=f"Item {item.id} is outside every room.",
                    item_ids=[item.id],
                )
            )
            continue
        inset = inset_polygon(room.polygon, rules.room_inset)
        if not all(point_in_polygon(corner, inset) for corner in item_obb(item, catalog).corners()):
            issues.append(
                LayoutIssue(
                    code="out_of_room",
                    severity="error",
                    message=f"Item {item.id} crosses a wall or the room boundary.",
                    item_ids=[item.id],
                )
            )

    seen_pairs: set[tuple[str, str]] = set()
    for index, first in enumerate(selected):
        if item_allows_overlap(first, catalog):
            continue
        first_box = item_obb(first, catalog)
        first_room = item_room_id(document, first)
        for second in selected[index + 1 :]:
            if item_allows_overlap(second, catalog):
                continue
            second_room = item_room_id(document, second)
            if first_room is not None and second_room is not None and first_room != second_room:
                continue
            pair: tuple[str, str] = (
                (first.id, second.id) if first.id < second.id else (second.id, first.id)
            )
            if pair in seen_pairs:
                continue
            second_box = item_obb(second, catalog)
            if obb_overlap(first_box, second_box, tolerance=COLLISION_TOLERANCE):
                seen_pairs.add(pair)
                issues.append(
                    LayoutIssue(
                        code="collision",
                        severity="error",
                        message=f"Items {pair[0]} and {pair[1]} overlap.",
                        item_ids=list(pair),
                    )
                )
                continue
            if (
                is_circulation_blocker(first, catalog)
                and is_circulation_blocker(second, catalog)
                and not catalog_items_grouped(first, second, catalog)
            ):
                gap = float(first_box.to_polygon().distance(second_box.to_polygon()))
                if 1e-6 < gap < rules.passage_secondary - 1e-6:
                    seen_pairs.add(pair)
                    issues.append(
                        LayoutIssue(
                            code="walkway_too_narrow",
                            severity="error",
                            message=(
                                f"Only {gap:.2f} m between {pair[0]} and {pair[1]} "
                                f"(minimum {rules.passage_secondary:.2f} m)."
                            ),
                            item_ids=list(pair),
                        )
                    )

    for opening in document.openings:
        room = rooms.get(opening.room_id)
        if room is None:
            continue
        if room_id is not None and opening.room_id != room_id:
            continue
        if opening.type == "door":
            zone = opening_clearance_obb(room, opening, rules)
            if zone is None:
                continue
            for item in selected:
                if item_allows_overlap(item, catalog):
                    continue
                if item_room_id(document, item) != opening.room_id:
                    continue
                if obb_overlap(item_obb(item, catalog), zone, tolerance=COLLISION_TOLERANCE):
                    issues.append(
                        LayoutIssue(
                            code="blocks_door",
                            severity="error",
                            message=f"Item {item.id} blocks the door {opening.id}.",
                            item_ids=[item.id],
                            opening_ids=[opening.id],
                        )
                    )
        else:
            zone = window_clearance_obb(room, opening, rules)
            if zone is None:
                continue
            sill = opening.sill_height if opening.sill_height is not None else 0.9
            for item in selected:
                if item_allows_overlap(item, catalog):
                    continue
                if item_room_id(document, item) != opening.room_id:
                    continue
                if item_height(item, catalog) <= sill + 1e-6:
                    continue
                if obb_overlap(item_obb(item, catalog), zone, tolerance=COLLISION_TOLERANCE):
                    issues.append(
                        LayoutIssue(
                            code="blocks_window",
                            severity="error",
                            message=f"Item {item.id} blocks the window {opening.id}.",
                            item_ids=[item.id],
                            opening_ids=[opening.id],
                        )
                    )

    for item in selected:
        for rule_issue in check_item_rules(
            document,
            item_id=item.id,
            catalog=catalog,
            rules=rules,
        ):
            issues.append(
                LayoutIssue(
                    code=rule_issue.rule_id,
                    severity="warning",
                    message=rule_issue.message,
                    item_ids=[item.id],
                    rule_id=rule_issue.rule_id,
                )
            )

    issues.sort(
        key=lambda issue: (
            issue.code,
            tuple(sorted(issue.item_ids)),
            tuple(sorted(issue.opening_ids)),
            issue.message,
        )
    )
    return VerifyLayoutResult(
        ok=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )


__all__ = ["verify_layout"]

"""L3 design-common-sense rules (PRD §6.3).

Five P0 rules with stable ids. Manual edits treat them as warnings; agent
writes run with ``strict=True`` and turn an issue into an ``unrealistic``
refusal so the model can self-correct.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from shapely.geometry import Point, Polygon

from app.contracts.catalog import CatalogEntry
from app.contracts.rules import Rules
from app.contracts.scene import Item, SceneDocument, Vec2
from app.geometry.collision import item_allows_overlap, item_obb, item_size
from app.geometry.obb import OBB
from app.geometry.polygon import centroid, point_in_polygon


@dataclass(frozen=True)
class RuleIssue:
    rule_id: str
    message: str
    suggest: str
    item_id: str


def _entry(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> CatalogEntry | None:
    return catalog.get(item.kind_id) if catalog is not None else None


def _height(item: Item, catalog: Mapping[str, CatalogEntry] | None) -> float:
    entry = _entry(item, catalog)
    if entry is None:
        return 0.8
    variant = next(
        (candidate for candidate in entry.variants if candidate.id == item.variant_id),
        entry.variants[0],
    )
    return variant.size_m[2]


def _centre(item: Item) -> Vec2:
    return (item.transform.position[0], item.transform.position[2])


def _front(item: Item) -> Vec2:
    return (math.sin(item.transform.rotation_y), math.cos(item.transform.rotation_y))


def _back(item: Item) -> Vec2:
    front = _front(item)
    return (-front[0], -front[1])


def _room_for(document: SceneDocument, item: Item) -> str | None:
    position = _centre(item)
    for room in document.rooms:
        if point_in_polygon(position, room.polygon):
            return room.id
    return item.constraint.room_id if item.constraint is not None else None


def _items_in_same_room(
    document: SceneDocument,
    item: Item,
    catalog: Mapping[str, CatalogEntry] | None,
) -> list[Item]:
    room_id = _room_for(document, item)
    return [
        other
        for other in document.items
        if other.id != item.id
        and not item_allows_overlap(other, catalog)
        and (room_id is None or _room_for(document, other) in (None, room_id))
    ]


def _distance_between(
    first: Item, second: Item, catalog: Mapping[str, CatalogEntry] | None
) -> float:
    return float(
        item_obb(first, catalog).to_polygon().distance(item_obb(second, catalog).to_polygon())
    )


def _in_front_cone(
    item: Item,
    target: Vec2,
    *,
    max_distance: float,
    half_angle_degrees: float = 55.0,
) -> bool:
    centre = _centre(item)
    dx = target[0] - centre[0]
    dz = target[1] - centre[1]
    distance = math.hypot(dx, dz)
    if distance <= 1e-9 or distance > max_distance:
        return False
    front = _front(item)
    cosine = (front[0] * dx + front[1] * dz) / distance
    return cosine >= math.cos(math.radians(half_angle_degrees))


def _nearest(
    item: Item, others: list[Item], catalog: Mapping[str, CatalogEntry] | None
) -> Item | None:
    best: Item | None = None
    best_distance = math.inf
    for other in others:
        distance = _distance_between(item, other, catalog)
        if distance < best_distance:
            best = other
            best_distance = distance
    return best


def check_item_rules(
    document: SceneDocument,
    *,
    item_id: str,
    catalog: Mapping[str, CatalogEntry] | None,
    rules: Rules,
) -> list[RuleIssue]:
    """Run the five P0 rules for the changed item."""
    item = next((candidate for candidate in document.items if candidate.id == item_id), None)
    if item is None:
        return []
    entry = _entry(item, catalog)
    category = entry.category.value if entry is not None else ""
    kind = item.kind_id
    issues: list[RuleIssue] = []
    others = _items_in_same_room(document, item, catalog)

    # 1. Bed headboard should touch a wall.
    if category == "bed" or kind.startswith("bed_"):
        anchor = item.constraint.anchor if item.constraint is not None else None
        anchored = anchor is not None and (
            anchor.wall_id is not None or anchor.wall_label is not None
        )
        if not anchored:
            centre = _centre(item)
            back = _back(item)
            depth = item_size(item, catalog)[1]
            back_point = (centre[0] + back[0] * depth / 2.0, centre[1] + back[1] * depth / 2.0)
            room = next(
                (
                    candidate
                    for candidate in document.rooms
                    if point_in_polygon(centre, candidate.polygon)
                ),
                None,
            )
            if room is not None:
                boundary = Polygon(room.polygon).boundary
                if boundary.distance(Point(back_point)) > rules.wall_flush_gap + 0.05:
                    issues.append(
                        RuleIssue(
                            "bed_not_against_wall",
                            "The bed headboard is not against a wall.",
                            "Move the bed so its headboard touches a wall, or use against_wall.",
                            item.id,
                        )
                    )

    # 2. Wardrobe needs 0.6 m of clear space in front.
    if kind.startswith("wardrobe") or (category == "storage" and _height(item, catalog) >= 1.5):
        centre = _centre(item)
        front = _front(item)
        width, depth = item_size(item, catalog)
        front_centre = (
            centre[0] + front[0] * (depth / 2.0 + 0.3),
            centre[1] + front[1] * (depth / 2.0 + 0.3),
        )
        clearance = OBB(
            center=front_centre,
            size=(width, 0.6),
            yaw=item.transform.rotation_y,
        ).to_polygon()
        for other in others:
            if clearance.intersects(item_obb(other, catalog).to_polygon()):
                issues.append(
                    RuleIssue(
                        "wardrobe_no_clearance",
                        f"{other.id} blocks the wardrobe's 0.6 m front clearance.",
                        "Leave at least 0.6 m clear in front of the wardrobe.",
                        item.id,
                    )
                )
                break

    # 3. Nightstand should be within 0.6 m of a bed.
    if kind.startswith("nightstand") or ("nightstand" in kind):
        beds = [other for other in others if other.kind_id.startswith("bed_")]
        if not beds:
            issues.append(
                RuleIssue(
                    "nightstand_too_far",
                    "The nightstand has no bed to sit beside.",
                    "Place the nightstand within 0.6 m of a bed.",
                    item.id,
                )
            )
        else:
            bed = _nearest(item, beds, catalog)
            if bed is not None and _distance_between(item, bed, catalog) > 0.6:
                issues.append(
                    RuleIssue(
                        "nightstand_too_far",
                        "The nightstand is more than 0.6 m from the bed.",
                        "Move the nightstand next to the bed headboard.",
                        item.id,
                    )
                )

    # 4. Sofa should have a focus (TV wall, table, or explicit facing).
    if category == "sofa" or kind.startswith("sofa"):
        focus = item.constraint.facing if item.constraint is not None else None
        anchor = item.constraint.anchor if item.constraint is not None else None
        labelled_tv = (
            anchor is not None
            and anchor.wall_label is not None
            and any(token in anchor.wall_label.lower() for token in ("tv", "电视"))
        )
        front_item = any(
            other.kind_id.startswith(("coffee_table", "tv_stand", "dining_table", "round_table"))
            and _in_front_cone(item, _centre(other), max_distance=3.2)
            for other in others
        )
        if not (focus is not None or labelled_tv or front_item):
            issues.append(
                RuleIssue(
                    "sofa_no_focus",
                    "The sofa does not face a TV wall, table or explicit target.",
                    "Place the sofa against the TV wall or facing a coffee table.",
                    item.id,
                )
            )

    # 5. Coffee table gap to the sofa should be 0.3-0.6 m.
    if kind.startswith("coffee_table"):
        sofas = [other for other in others if other.kind_id.startswith("sofa")]
        if sofas:
            sofa = _nearest(item, sofas, catalog)
            if sofa is not None:
                gap = _distance_between(item, sofa, catalog)
                if gap < 0.3 - 1e-6 or gap > 0.6 + 1e-6:
                    issues.append(
                        RuleIssue(
                            "coffee_table_gap",
                            f"The coffee table is {gap:.2f} m from the sofa (expected 0.3-0.6 m).",
                            "Move the coffee table to leave a 0.3-0.6 m gap.",
                            item.id,
                        )
                    )

    return issues


def layout_centroid(document: SceneDocument, room_id: str) -> Vec2 | None:
    """Convenience helper used by placement and tests."""
    room = next((candidate for candidate in document.rooms if candidate.id == room_id), None)
    return centroid(room.polygon) if room is not None else None

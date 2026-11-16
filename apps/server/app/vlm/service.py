"""VLM review closed loop (PRD §6.6, M4-04).

Flow: labelled orthographic screenshot -> JSON issues -> ID validation ->
server-side geometry recheck -> confirmed issues mapped to a local solver run ->
``verify_layout`` + main-path recheck (at most two rounds).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.capture.service import CaptureImage
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import ItemSource
from app.contracts.layout import (
    AutoLayoutPlacement,
    AutoLayoutResult,
    VLMVisualIssue,
    VLMVisualReview,
)
from app.contracts.patch import ItemUpdateFields, ItemUpdateOp, PatchOp, TransformUpdate
from app.contracts.rules import Rules
from app.contracts.scene import ItemConstraint, Room, SceneDocument
from app.core.config import Settings
from app.geometry.collision import item_room_id
from app.solver.circulation import room_blocking_item_ids, room_path_blocked
from app.solver.engine import SolveOptions, SolveSpec
from app.solver.service import SolverService
from app.solver.verify import verify_layout
from app.vlm.client import VLMClient
from app.vlm.recheck import issue_confirmed

logger = structlog.get_logger(__name__)

ISSUE_TYPES = (
    "blocks_door",
    "walkway_narrow",
    "group_misaligned",
    "circulation_blocked",
    "window_blocked",
)
SEVERITIES = ("high", "medium", "low")

REVIEW_PROMPT = """You are a strict interior-layout reviewer.
Inspect the attached orthographic top-view screenshot of one room.

Labels in the image:
- Furniture is labelled A1, A2, ...; doors D1, D2, ...; windows W1, W2, ...
- The legend below maps every label to the server id and kind.

Return ONLY a JSON object with this schema (no markdown fences):
{{
  "verdict": "pass" | "fail",
  "issues": [
    {{
      "type": "blocks_door" | "walkway_narrow" | "group_misaligned" | "circulation_blocked" | "window_blocked",
      "severity": "high" | "medium" | "low",
      "itemIds": ["A1"],
      "openingIds": ["D1"],
      "evidence": "short visual evidence",
      "suggest": "short fix"
    }}
  ]
}}

Rules:
1. Only report visually obvious problems. Do not invent items. Use labels from the image.
2. Never output coordinates or numeric placement advice; the server decides positions.
3. `circulation_blocked` means the route between a door and the room's main area or a
   window is visually obstructed. Use it for a clear blocked main path, not ordinary
   narrow gaps.
4. If the layout looks acceptable, return {{"verdict": "pass", "issues": []}}.

Legend:
{legend}

Room:
{room}
"""


@dataclass
class VLMReviewResult:
    review: VLMVisualReview
    placements: list[AutoLayoutPlacement] = field(default_factory=list)
    affected_item_ids: list[str] = field(default_factory=list)

    def update_ops(self, document: SceneDocument) -> list[PatchOp]:
        by_id = {item.id: item for item in document.items}
        ops: list[PatchOp] = []
        for placement in self.placements:
            existing = by_id.get(placement.item_id)
            if existing is None:
                continue
            ops.append(
                ItemUpdateOp(
                    op="item.update",
                    item_id=placement.item_id,
                    after=ItemUpdateFields(
                        transform=TransformUpdate(
                            position=placement.transform.position,
                            rotation_y=placement.transform.rotation_y,
                        ),
                        constraint=placement.constraint,
                    ),
                )
            )
        return ops


class VLMService:
    def __init__(self, client: VLMClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def client(self) -> VLMClient:
        """Underlying adapter, shared by other VLM consumers (floor-plan parse)."""
        return self._client

    @property
    def enabled(self) -> bool:
        return True

    async def review_and_replan(
        self,
        document: SceneDocument,
        room: Room,
        image: CaptureImage,
        catalog: Mapping[str, CatalogEntry],
        rules: Rules,
        solver: SolverService,
        *,
        legend: dict[str, Any] | None = None,
    ) -> VLMReviewResult:
        legend = legend or build_legend(document, room)
        prompt = REVIEW_PROMPT.format(
            legend=json.dumps(legend, ensure_ascii=False, indent=2),
            room=json.dumps(
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type.value,
                    "polygon": room.polygon,
                    "north": document.north,
                },
                ensure_ascii=False,
            ),
        )
        raw, attempts, provider_failed = await self._request_json(prompt, image.png)
        if raw is None:
            return VLMReviewResult(
                review=VLMVisualReview(
                    status="unavailable" if provider_failed else "invalid_json",
                    model=self._client.model,
                    parse_attempts=attempts,
                )
            )
        payload = _parse_json(raw)
        if payload is None:
            return VLMReviewResult(
                review=VLMVisualReview(
                    status="invalid_json",
                    model=self._client.model,
                    parse_attempts=attempts,
                )
            )
        confirmed, unconfirmed, unknown = await asyncio.to_thread(
            self._validate_issues,
            document,
            room,
            payload,
            legend,
            catalog,
            rules,
        )
        if not confirmed:
            return VLMReviewResult(
                review=VLMVisualReview(
                    status="passed",
                    model=self._client.model,
                    parse_attempts=attempts,
                    confirmed=[],
                    unconfirmed_count=unconfirmed,
                    unknown_id_count=unknown,
                )
            )
        review = VLMVisualReview(
            status="issues_found",
            model=self._client.model,
            parse_attempts=attempts,
            confirmed=confirmed,
            unconfirmed_count=unconfirmed,
            unknown_id_count=unknown,
        )
        placements: list[AutoLayoutPlacement] = []
        affected = sorted({item_id for issue in confirmed for item_id in issue.item_ids})
        rounds = min(2, max(1, self._settings.vlm_max_rounds))
        for round_index in range(1, rounds + 1):
            review.rounds = round_index
            result = await self._replan(
                document,
                room,
                catalog,
                rules,
                solver,
                affected,
            )
            if result is None:
                break
            placements = result.placements
            scratch = _apply_placements(document, result)
            geometry = await asyncio.to_thread(
                verify_layout, scratch, catalog, rules, room_id=room.id
            )
            path_blocked = await asyncio.to_thread(room_path_blocked, scratch, room, catalog, rules)
            if geometry.ok and not path_blocked:
                review.status = "replanned"
                review.rechecked = True
                review.proposal_id = None
                return VLMReviewResult(
                    review=review,
                    placements=placements,
                    affected_item_ids=sorted(set(affected)),
                )
            extra = {
                item_id
                for issue in geometry.issues
                for item_id in issue.item_ids
                if issue.severity == "error"
            }
            if path_blocked:
                extra.update(
                    await asyncio.to_thread(
                        room_blocking_item_ids,
                        scratch,
                        room,
                        catalog,
                        rules,
                        candidates=affected,
                        limit=2,
                    )
                )
                if not extra:
                    extra.update(
                        await asyncio.to_thread(
                            room_blocking_item_ids,
                            scratch,
                            room,
                            catalog,
                            rules,
                            limit=2,
                        )
                    )
            new_affected = sorted(set(affected) | extra)
            if new_affected == affected:
                break
            affected = new_affected
        review.status = "issues_found"
        review.rechecked = False
        return VLMReviewResult(review=review, placements=placements, affected_item_ids=affected)

    async def _request_json(self, prompt: str, png: bytes) -> tuple[str | None, int, bool]:
        attempts = 0
        last_error = ""
        provider_failed = False
        current_prompt = prompt
        for attempts in range(1, 3):
            try:
                raw = await self._client.review(
                    png,
                    current_prompt,
                    timeout=self._settings.vlm_timeout_seconds,
                )
            except Exception as error:
                logger.info("vlm.request_failed", error=str(error), attempt=attempts)
                last_error = str(error)
                provider_failed = True
                continue
            provider_failed = False
            if _parse_json(raw) is not None:
                return raw, attempts, False
            last_error = "response was not a JSON object"
            current_prompt = (
                prompt
                + "\n\nYour previous answer could not be parsed as JSON: "
                + raw[:500]
                + "\nReturn ONLY valid JSON matching the schema."
            )
        logger.info("vlm.invalid_json", error=last_error)
        return None, attempts, provider_failed

    def _validate_issues(
        self,
        document: SceneDocument,
        room: Room,
        payload: dict[str, Any],
        legend: dict[str, Any],
        catalog: Mapping[str, CatalogEntry],
        rules: Rules,
    ) -> tuple[list[VLMVisualIssue], int, int]:
        item_labels = {entry.get("label", ""): entry.get("itemId", "") for entry in legend["items"]}
        opening_labels = {
            entry.get("label", ""): entry.get("openingId", "") for entry in legend["openings"]
        }
        item_ids = {item.id for item in document.items if item_room_id(document, item) == room.id}
        opening_ids = {opening.id for opening in document.openings if opening.room_id == room.id}
        issues = payload.get("issues")
        if not isinstance(issues, list):
            return [], 0, 0
        confirmed: list[VLMVisualIssue] = []
        seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
        unconfirmed = 0
        unknown = 0
        for raw in issues[:20]:
            if not isinstance(raw, dict):
                unconfirmed += 1
                continue
            issue_type = str(raw.get("type", ""))
            if issue_type not in ISSUE_TYPES:
                unconfirmed += 1
                continue
            severity = str(raw.get("severity", "medium"))
            if severity not in SEVERITIES:
                severity = "medium"
            mapped_items: list[str] = []
            for value in _string_list(raw.get("itemIds"))[:8]:
                resolved = item_labels.get(value, value)
                if resolved in item_ids:
                    if resolved not in mapped_items:
                        mapped_items.append(resolved)
                else:
                    unknown += 1
            mapped_openings: list[str] = []
            for value in _string_list(raw.get("openingIds"))[:8]:
                resolved = opening_labels.get(value, value)
                if resolved in opening_ids:
                    if resolved not in mapped_openings:
                        mapped_openings.append(resolved)
                else:
                    unknown += 1
            if not mapped_items:
                unconfirmed += 1
                continue
            ok = issue_confirmed(
                document,
                room,
                catalog,
                rules,
                issue_type=issue_type,
                item_ids=mapped_items,
                opening_ids=mapped_openings,
            )
            if not ok:
                unconfirmed += 1
                continue
            key = (issue_type, tuple(sorted(mapped_items)), tuple(sorted(mapped_openings)))
            if key in seen:
                continue
            seen.add(key)
            confirmed.append(
                VLMVisualIssue(
                    type=issue_type,
                    severity=severity,
                    item_ids=mapped_items,
                    opening_ids=mapped_openings,
                    confirmed=True,
                    evidence=str(raw.get("evidence", ""))[:500],
                    suggest=str(raw.get("suggest", ""))[:500],
                )
            )
        return confirmed, unconfirmed, unknown

    async def _replan(
        self,
        document: SceneDocument,
        room: Room,
        catalog: Mapping[str, CatalogEntry],
        rules: Rules,
        solver: SolverService,
        affected: list[str],
    ) -> AutoLayoutResult | None:
        by_id = {item.id: item for item in document.items}
        specs: list[SolveSpec] = []
        for item_id in affected:
            item = by_id.get(item_id)
            if item is None or item_room_id(document, item) != room.id:
                continue
            constraint = None
            if item.constraint is not None:
                # Keep declared facing; drop anchors that may have caused the
                # issue so the local solver can move the piece.
                constraint = ItemConstraint(
                    room_id=room.id,
                    facing=item.constraint.facing,
                    clearance=item.constraint.clearance,
                    group=item.constraint.group,
                )
            specs.append(
                SolveSpec(
                    kind_id=item.kind_id,
                    variant_id=item.variant_id,
                    required=False,
                    constraint=constraint,
                    existing_item_id=item.id,
                )
            )
        if not specs:
            return None
        try:
            return await solver.solve(
                document,
                dict(catalog),
                rules,
                room_id=room.id,
                items=specs,
                options=SolveOptions(
                    allow_partial=True,
                    time_budget_ms=rules.solver_time_budget,
                ),
            )
        except Exception as error:  # noqa: BLE001 - local replan failure is non-fatal
            logger.info("vlm.replan_failed", error=str(error), room_id=room.id)
            return None


def build_legend(document: SceneDocument, room: Room) -> dict[str, Any]:
    items = [item for item in document.items if item_room_id(document, item) == room.id]
    items.sort(key=lambda item: item.id)
    items_legend = [
        {"label": f"A{index}", "itemId": item.id, "kindId": item.kind_id}
        for index, item in enumerate(items, start=1)
    ]
    openings = [opening for opening in document.openings if opening.room_id == room.id]
    openings.sort(key=lambda opening: opening.id)
    counters = {"door": 0, "window": 0}
    openings_legend: list[dict[str, Any]] = []
    for opening in openings:
        counters[opening.type.value] += 1
        prefix = "D" if opening.type.value == "door" else "W"
        openings_legend.append(
            {
                "label": f"{prefix}{counters[opening.type.value]}",
                "openingId": opening.id,
                "type": opening.type.value,
            }
        )
    return {"items": items_legend, "openings": openings_legend}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(entry) for entry in value]
    return []


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _apply_placements(document: SceneDocument, result: AutoLayoutResult) -> SceneDocument:
    by_id = {placement.item_id: placement for placement in result.placements}
    items = []
    for item in document.items:
        placement = by_id.get(item.id)
        if placement is None:
            items.append(item)
            continue
        items.append(
            item.model_copy(
                update={
                    "transform": placement.transform,
                    "constraint": placement.constraint or item.constraint,
                    "source": ItemSource.AGENT,
                }
            )
        )
    return document.model_copy(update={"items": items})


__all__ = ["VLMReviewResult", "VLMService", "build_legend"]

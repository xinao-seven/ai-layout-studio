"""System prompt and scene snapshot construction (PRD §6.2, M3-02)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from app.contracts.catalog import CatalogEntry
from app.contracts.proposal import Proposal
from app.contracts.scene import Item, SceneDocument
from app.geometry.polygon import centroid
from app.geometry.walls import derive_wall_segments

TOKEN_BUDGET = 120_000
COMPACT_AT = 0.7
MAX_TOOL_RESULT_TOKENS = 4_000

SYSTEM_PROMPT = """You are an interior layout assistant working inside a 3D home editor.
You translate natural-language requests into semantic tool calls; the deterministic
server computes coordinates.

Rules:
1. Read before writing. Call query_scene to learn room/item/wall/opening ids and
   search_catalog to learn kindIds, variants and sizes. Never invent ids.
2. Never output raw coordinates. add_item and move_item accept relations:
   against_wall (wallId or wallLabel), near_window, near_item, corner, center.
   move_item may alternatively use a metric offset {dx,dz}.
3. Every write tool returns a proposal shown as a ghost. The user confirms or
   rejects it, so do not claim the item has been placed until the tool result
   says proposalCreated. Pending proposals are already part of the overlay and
   may be referenced by later near_item relations.
4. If a tool returns refusals, read every refusal and self-correct. Do not repeat
   the exact same failing call. Prefer smaller variants, another wall, or a
   nearby free position.
5. Use ask_user only when an ambiguity materially changes the result (for
   example which room, or two very different styles). Otherwise pick a sensible
   default and continue.
6. Answer in the user's language. Keep the final message short: what you
   proposed, what still needs confirmation, and any refusal the user must know.
7. When the request is complete, finish with a plain text message and no tool
   calls. Do not create duplicate proposals for the same request.
8. Use auto_layout for whole-room furnishing requests ("布置好卧室"). Pass every
   required piece and semantic constraints such as facing="south"; do not call
   add_item once per piece. The solver returns one proposal containing all
   placements, which the user confirms atomically.
9. Use verify_layout to audit an existing layout. Set visual=true only when the
   user asks to check or inspect the layout visually; it captures a labelled
   top view, runs the VLM review and may return a local re-layout proposal. If
   the tool reports capture_unavailable or invalid_json, rely on the geometric
   issues and tell the user briefly.
10. After auto_layout returns a proposal, treat it as final for this turn. Do not
   move, rotate or re-add the items it just placed; the user confirms or rejects
   the whole proposal. If verify_layout returns ok=true, stop editing even when
   it has warning-severity suggestions: report the warnings and finish.
"""


def estimate_tokens(text: str) -> int:
    """Conservative character-based budget estimate (CJK /3, ASCII /4)."""
    ascii_count = sum(1 for character in text if ord(character) < 128)
    other_count = len(text) - ascii_count
    return int(ascii_count / 4) + int(other_count / 3) + 1


def compact_tool_result(payload: Any) -> Any:
    """Truncate an oversized tool result to the context budget."""
    text = str(payload)
    if estimate_tokens(text) <= MAX_TOOL_RESULT_TOKENS:
        return payload
    if isinstance(payload, dict):
        compacted: dict[str, Any] = {}
        for key, value in payload.items():
            compacted[key] = compact_tool_result(value)
        compacted["truncated"] = True
        if estimate_tokens(str(compacted)) <= MAX_TOOL_RESULT_TOKENS:
            return compacted
    return {"truncated": True, "summary": text[: MAX_TOOL_RESULT_TOKENS * 3]}


def _item_payload(item: Item, *, pending: bool) -> dict[str, Any]:
    constraint = item.constraint.model_dump(mode="json", by_alias=True) if item.constraint else None
    return {
        "id": item.id,
        "kindId": item.kind_id,
        "variantId": item.variant_id,
        "position": [round(value, 3) for value in item.transform.position],
        "rotationY": round(item.transform.rotation_y, 4),
        "constraint": constraint,
        "source": item.source.value,
        "pending": pending,
    }


def _catalog_summary(catalog: Mapping[str, CatalogEntry]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in catalog.values():
        variant = entry.variants[0]
        default = next(
            (candidate for candidate in entry.variants if candidate.id == entry.default_variant_id),
            variant,
        )
        grouped[entry.category.value].append(
            {
                "kindId": entry.kind_id,
                "name": entry.name,
                "sizeM": default.size_m,
                "mustAgainstWall": bool(entry.rules and entry.rules.must_against_wall),
            }
        )
    return [
        {"category": category, "items": items[:20]} for category, items in sorted(grouped.items())
    ]


class ContextBuilder:
    """Builds the dynamic system prompt and scene snapshot."""

    def __init__(self, catalog: Mapping[str, CatalogEntry]) -> None:
        self._catalog = catalog

    def scene_snapshot(
        self,
        document: SceneDocument,
        proposals: list[Proposal],
        *,
        pending_ids: set[str],
    ) -> dict[str, Any]:
        rooms: list[dict[str, Any]] = []
        for room in document.rooms:
            segments = derive_wall_segments(room, document.defaults)
            labels = room.wall_labels or {}
            rooms.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type.value,
                    "polygon": room.polygon,
                    "centre": [round(value, 3) for value in centroid(room.polygon)],
                    "walls": [
                        {
                            "id": segment.id,
                            "edgeIndex": segment.edge_index,
                            "label": labels.get(str(segment.edge_index)),
                            "length": round(segment.length, 3),
                            "normal": [round(value, 3) for value in segment.normal],
                        }
                        for segment in segments
                    ],
                }
            )
        return {
            "sceneId": document.id,
            "name": document.name,
            "version": document.version,
            "north": document.north,
            "rooms": rooms,
            "openings": [
                {
                    "id": opening.id,
                    "type": opening.type.value,
                    "roomId": opening.room_id,
                    "edgeIndex": opening.edge_index,
                    "offset": opening.offset,
                    "width": opening.width,
                    "height": opening.height,
                    "sillHeight": opening.sill_height,
                }
                for opening in document.openings
            ],
            "items": [
                _item_payload(item, pending=item.id in pending_ids) for item in document.items
            ],
            "pendingProposals": [
                {
                    "id": proposal.id,
                    "status": proposal.status.value,
                    "baseVersion": proposal.base_version,
                    "dependsOn": proposal.depends_on,
                    "ops": [op.model_dump(mode="json", by_alias=True) for op in proposal.ops],
                }
                for proposal in proposals
            ],
        }

    def system_prompt(
        self,
        document: SceneDocument,
        proposals: list[Proposal],
        *,
        pending_ids: set[str] | None = None,
        point_context: dict[str, Any] | None = None,
    ) -> str:
        snapshot = self.scene_snapshot(
            document,
            proposals,
            pending_ids=pending_ids or set(),
        )
        sections = [
            SYSTEM_PROMPT,
            "Scene snapshot (authoritative, includes pending proposals):",
            _json(snapshot),
            "Catalog summary (use search_catalog for full details):",
            _json(_catalog_summary(self._catalog)),
        ]
        if point_context:
            sections.extend(
                [
                    "The user selected this item/context in the editor:",
                    _json(point_context),
                ]
            )
        return "\n\n".join(sections)


def _json(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

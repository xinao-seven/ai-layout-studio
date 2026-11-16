"""Stable refusal codes and their non-empty suggestions (PRD §6.3).

Every code in the v1.1 registry has a default suggestion. Tools may pass a
more specific message/details, but ``suggest`` is never empty.
"""

from __future__ import annotations

from typing import Any

from app.contracts.proposal import Refusal

MAX_REFUSALS = 5

REFUSAL_SUGGESTS: dict[str, str] = {
    "invalid_input": "Fix the fields and units to match the tool schema, then retry.",
    "not_found": "Call query_scene for the latest item/opening ids.",
    "unknown_kind": "Call search_catalog with a keyword to find a valid kindId.",
    "variant_missing": "Use the catalog defaultVariantId or search for a similar kind.",
    "unknown_room": "Call query_scene to get the current room ids.",
    "invalid_polygon": "Fix the vertex order or redraw the room.",
    "room_too_small": "Enlarge the room before placing furniture.",
    "unsupported_room_shape": "Use manual placement plus verify_layout, or split the room.",
    "out_of_room": "Move the item toward the room centre or use a smaller variant.",
    "collision": "Slide the item along the wall or choose an empty position.",
    "no_space": "Remove another item, use a smaller variant, or choose a different wall.",
    "blocks_door": "Keep at least 0.6 m clear in front of the door, or use another wall.",
    "blocks_window": "Use a lower item below the sill or move it away from the window.",
    "walkway_too_narrow": "Increase the gap between items to at least 0.6 m.",
    "item_too_large": "Use a smaller variant or remove items occupying the wall.",
    "opening_overlap": "Adjust the opening offset or width.",
    "opening_off_wall": "Place the opening fully on an existing wall segment.",
    "solver_timeout": "Reduce the number of required items or relax the constraints.",
    "unrealistic": "Follow the rule suggestion to make the layout realistic.",
    "proposal_expired": "Regenerate the proposal from the latest scene version.",
    "version_conflict": "Fetch the latest scene snapshot and retry.",
    "turn_in_progress": "Wait for the running turn or cancel it first.",
    "turn_cancelled": "Send the request again when the user is ready.",
    "budget_exceeded": "Narrow the request to fewer rooms or fewer items.",
    "llm_error": "Retry once; if it persists, check LLM_BASE_URL/LLM_API_KEY/LLM_MODEL.",
    "tool_timeout": "Reduce the scope of the request and retry.",
    "capture_unavailable": "Skip the VLM check and rely on geometric verification.",
    "capture_invalid": "Retry the screenshot; the PNG was missing, corrupt or too large.",
    "vlm_invalid_json": "Record the failure and continue without blocking the user.",
    "vlm_unavailable": "Configure VLM_BASE_URL/VLM_MODEL or skip the visual check.",
    "vlm_unknown_id": "Ignore the unknown label and keep the geometric checks.",
    "vlm_issue_unconfirmed": "Treat the VLM issue as a false positive and keep the layout.",
    "scene_changed": "Fetch the latest scene and regenerate pending proposals.",
    "import_invalid": "Fix the document fields or use a supported schema version.",
    "internal_error": "Retry once; if it persists, check the server logs.",
}


def make_refusal(
    code: str,
    message: str,
    *,
    suggest: str | None = None,
    details: dict[str, Any] | None = None,
) -> Refusal:
    """Build a refusal with a guaranteed non-empty suggestion."""
    suggestion = (suggest or REFUSAL_SUGGESTS.get(code) or "").strip()
    if not suggestion:
        suggestion = REFUSAL_SUGGESTS["internal_error"]
    return Refusal(
        code=code,
        message=message,
        suggest=suggestion,
        details=details or None,
    )


def refusal_from_error(
    code: str,
    message: str,
    suggest: str | None,
    details: dict[str, Any] | None,
) -> Refusal:
    """Adapter used when a SceneError/GeometryError crosses the tool boundary."""
    return make_refusal(code, message, suggest=suggest, details=details)


def dedupe_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Keep the first occurrence of each (code, message) pair, capped at five."""
    seen: set[tuple[str, str]] = set()
    unique: list[Refusal] = []
    for refusal in refusals:
        key = (refusal.code, refusal.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(refusal)
        if len(unique) >= MAX_REFUSALS:
            break
    return unique

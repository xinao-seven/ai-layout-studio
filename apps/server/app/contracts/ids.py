"""Server-minted identifiers (PRD §4.3)."""

from __future__ import annotations

import secrets
from enum import StrEnum


class IdPrefix(StrEnum):
    SCENE = "scene_"
    ROOM = "room_"
    OPENING = "op_"
    ITEM = "it_"
    PROPOSAL = "prop_"
    TURN = "turn_"
    WALL_SEGMENT = "seg_"


def new_id(prefix: IdPrefix) -> str:
    """Mint a new server-side identifier."""
    return f"{prefix.value}{secrets.token_hex(6)}"


def wall_segment_id(room_id: str, edge_index: int) -> str:
    """Stable wall segment id derived from a room polygon edge (PRD §5.2)."""
    return f"{room_id}:e{edge_index}"

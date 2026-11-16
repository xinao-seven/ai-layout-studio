"""Shared enums used by scene, API and WebSocket contracts."""

from __future__ import annotations

from enum import StrEnum


class RoomType(StrEnum):
    LIVING = "living"
    BEDROOM = "bedroom"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    STUDY = "study"
    BALCONY = "balcony"
    OTHER = "other"


class OpeningType(StrEnum):
    DOOR = "door"
    WINDOW = "window"


class DoorSwing(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    SLIDING = "sliding"


class DoorHinge(StrEnum):
    START = "start"
    END = "end"


class OpensTo(StrEnum):
    IN = "in"
    OUT = "out"


class ItemSource(StrEnum):
    MANUAL = "manual"
    AGENT = "agent"


class Actor(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AnchorType(StrEnum):
    WALL = "wall"
    CORNER = "corner"
    WINDOW = "window"
    DOOR = "door"
    CENTER = "center"


class FacingDirection(StrEnum):
    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


class FacingToward(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    ITEM = "item"


class CatalogCategory(StrEnum):
    SOFA = "sofa"
    BED = "bed"
    TABLE = "table"
    STORAGE = "storage"
    LIGHTING = "lighting"
    DECOR = "decor"
    OTHER = "other"

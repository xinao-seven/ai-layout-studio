"""ORM models for the six persistence tables (PRD §5.5).

JSON documents are stored as SQLite JSON columns so the schema stays close to
the contract while remaining queryable on SQLite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp used as a Python-side default."""
    return datetime.now(UTC)


class Scene(Base):
    """Current confirmed scene snapshot and version pointer."""

    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    doc_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class SceneVersion(Base):
    """Append-only version history (latest 100 retained)."""

    __tablename__ = "scene_versions"

    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    patch_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    inverse_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class Turn(Base):
    """Agent turn state and usage."""

    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    messages_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class TurnEvent(Base):
    """Replayable turn event stream (max 500 events per turn)."""

    __tablename__ = "turn_events"

    turn_id: Mapped[str] = mapped_column(
        ForeignKey("turns.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class Proposal(Base):
    """Pending/confirmed/rejected proposal written by the agent."""

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_id: Mapped[str] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    depends_on_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ops_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (Index("ix_proposals_scene_id_status", "scene_id", "status"),)


class EvalRun(Base):
    """Evaluation report records (PRD §6.8)."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

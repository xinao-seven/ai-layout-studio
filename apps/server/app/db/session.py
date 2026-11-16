"""Async engine and session factory with SQLite safety pragmas."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any = None) -> None:
    """Enable WAL, foreign keys and a busy timeout on a raw SQLite connection.

    Shared by the runtime engine and the Alembic migration engine so a freshly
    migrated database is already in WAL mode (PRD §5.5).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_engine(database_url: str | None = None) -> AsyncEngine:
    """Create an async engine with SQLite safety pragmas attached."""
    engine = create_async_engine(database_url or get_settings().database_url)

    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", apply_sqlite_pragmas)

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

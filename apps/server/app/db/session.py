"""Async engine and session factory with SQLite safety pragmas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings


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


def ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create the parent directory of a file-backed SQLite database."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    if url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def create_engine(
    database_url: str | None = None,
    *,
    settings: Settings | None = None,
) -> AsyncEngine:
    """Create an async engine with SQLite safety pragmas attached.

    Pass ``settings`` (typically ``request.app.state.settings``) so a test or
    development instance uses its own data directory instead of the cached
    process-wide settings.
    """
    resolved_url = database_url or (settings or get_settings()).database_url
    ensure_sqlite_parent_dir(resolved_url)
    engine = create_async_engine(resolved_url)

    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", apply_sqlite_pragmas)

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

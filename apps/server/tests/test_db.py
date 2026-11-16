"""Database engine, pragma, timestamp and migration tests."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import Settings
from app.db import models  # noqa: F401  (registers tables)
from app.db.base import Base
from app.db.models import Scene
from app.db.session import create_engine, create_session_factory

EXPECTED_TABLES = {
    "scenes",
    "scene_versions",
    "turns",
    "turn_events",
    "proposals",
    "eval_runs",
}


async def test_create_all_creates_six_tables_and_enables_pragmas(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'tables.db').as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar()
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar()
            names = set(
                (
                    await connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type = 'table'")
                    )
                ).scalars()
            )
    finally:
        await engine.dispose()

    assert journal_mode is not None and str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5000
    assert names >= EXPECTED_TABLES


async def test_session_factory_yields_usable_session(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'session.db').as_posix()}")
    factory = create_session_factory(engine)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


async def test_engine_uses_injected_settings(test_settings: Settings, tmp_path: Path) -> None:
    settings = test_settings.model_copy(update={"data_dir": tmp_path / "injected"})
    engine = create_engine(settings=settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    assert (settings.data_dir / "app.db").exists()


async def test_timestamps_round_trip_as_aware_utc(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'timestamps.db').as_posix()}")
    factory = create_session_factory(engine)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(Scene(id="scene_1", name="Test", current_version=1, doc_json={}))
            await session.commit()
        async with factory() as session:
            scene = await session.get(Scene, "scene_1")
            assert scene is not None
            assert scene.created_at.tzinfo is not None
            assert scene.created_at.utcoffset() == timedelta(0)
            assert scene.updated_at.tzinfo is not None
            assert scene.updated_at.utcoffset() == timedelta(0)
    finally:
        await engine.dispose()


def test_alembic_migration_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "migrated.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))

    command.upgrade(config, "head")
    command.check(config)  # fails if models and migrations drift
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables
    assert str(journal_mode).lower() == "wal"

    command.downgrade(config, "base")
    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'alembic_version'"
            )
        }
    assert remaining == set()

"""Run the server with settings-driven host/port and a single worker.

Usage (from ``apps/server``):

    python -m app             # apply migrations, then serve
    python -m app --reload    # development auto-reload

``HOST`` and ``PORT`` are read from the environment or a .env file, so the
values documented in .env.example actually take effect.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from app.core.config import get_settings


def _run_migrations() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI layout server.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (development only).",
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Do not apply the Alembic migration chain before serving.",
    )
    args = parser.parse_args()

    if not args.skip_migrations:
        _run_migrations()

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

"""Run the server with settings-driven host/port and a single worker.

Usage (from ``apps/server``):

    python -m app             # production-ish
    python -m app --reload    # development auto-reload

``HOST`` and ``PORT`` are read from the environment or a .env file, so the
values documented in .env.example actually take effect.
"""

from __future__ import annotations

import argparse

import uvicorn

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI layout server.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (development only).",
    )
    args = parser.parse_args()

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

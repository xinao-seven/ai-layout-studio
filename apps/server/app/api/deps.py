"""FastAPI dependency helpers."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from app.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Return the Settings instance bound to the current application."""
    return cast(Settings, request.app.state.settings)

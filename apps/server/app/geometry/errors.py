"""Geometry errors with stable refusal codes."""

from __future__ import annotations

from typing import Any


class GeometryError(Exception):
    """A geometry validation failure carrying a refusal code."""

    def __init__(
        self,
        code: str,
        message: str,
        suggest: str = "Adjust the geometry and try again.",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggest = suggest
        self.details = details or {}

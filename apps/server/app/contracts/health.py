"""Health-check contract."""

from __future__ import annotations

from pydantic import Field

from app.contracts.base import CamelModel


class HealthResponse(CamelModel):
    """Response body for ``GET /api/health`` (PRD §6.7)."""

    ok: bool = Field(description="Service is accepting requests.")
    version: str = Field(description="Application version.")
    model: str = Field(description="Configured LLM model name; empty when not set.")

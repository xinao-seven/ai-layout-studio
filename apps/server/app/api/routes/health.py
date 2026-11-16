"""Health endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_app_settings
from app.contracts.health import HealthResponse
from app.core.config import Settings

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> HealthResponse:
    """Liveness probe used by the dev loop, Docker healthcheck and CI."""
    return HealthResponse(
        ok=True,
        version=settings.app_version,
        model=settings.llm_model,
    )

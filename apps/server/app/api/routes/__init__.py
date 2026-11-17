"""REST API routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.agent import router as agent_router
from app.api.routes.health import router as health_router
from app.api.routes.meta import router as meta_router
from app.api.routes.scenes import router as scenes_router
from app.api.routes.screenshots import router as screenshots_router
from app.api.routes.turns import router as turns_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(agent_router)
api_router.include_router(scenes_router)
api_router.include_router(meta_router)
api_router.include_router(turns_router)
api_router.include_router(screenshots_router)

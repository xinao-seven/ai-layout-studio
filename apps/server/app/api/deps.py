"""FastAPI dependency helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request

from app.catalog.service import CatalogService
from app.core.config import Settings
from app.db.repository import SceneRepository
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry
from app.sync.gateway import SyncGateway

if TYPE_CHECKING:
    from app.agent.manager import TurnManager
    from app.vlm.service import VLMService


def get_app_settings(request: Request) -> Settings:
    """Return the Settings instance bound to the current application."""
    return cast(Settings, request.app.state.settings)


def get_vlm(request: Request) -> VLMService | None:
    """Return the VLM service, or None when it is not configured."""
    service = getattr(request.app.state, "vlm", None)
    return cast("VLMService | None", service)


def get_repository(request: Request) -> SceneRepository:
    return cast(SceneRepository, request.app.state.repository)


def get_registry(request: Request) -> SceneRegistry:
    return cast(SceneRegistry, request.app.state.registry)


def get_catalog(request: Request) -> CatalogService:
    return cast(CatalogService, request.app.state.catalog)


def get_fixtures(request: Request) -> FixtureService:
    return cast(FixtureService, request.app.state.fixtures)


def get_gateway(request: Request) -> SyncGateway:
    return cast(SyncGateway, request.app.state.gateway)


def get_turn_manager(request: Request) -> TurnManager:
    return cast("TurnManager", request.app.state.turn_manager)

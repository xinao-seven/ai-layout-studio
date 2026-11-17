"""Catalog and built-in fixture endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.manager import TurnManager
from app.api.deps import get_catalog, get_fixtures, get_registry, get_turn_manager
from app.catalog.service import CatalogService
from app.contracts.api import CatalogResponse, FixtureListResponse
from app.scene.errors import SceneError
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry

router = APIRouter(tags=["meta"])


@router.get("/fixtures", response_model=FixtureListResponse)
async def list_fixtures(
    fixtures: Annotated[FixtureService, Depends(get_fixtures)],
) -> FixtureListResponse:
    return FixtureListResponse(fixtures=fixtures.list_fixtures())


@router.get("/catalog", response_model=CatalogResponse)
async def list_catalog(
    catalog: Annotated[CatalogService, Depends(get_catalog)],
) -> CatalogResponse:
    return CatalogResponse(entries=catalog.entries)


@router.post("/catalog/reload", response_model=CatalogResponse)
async def reload_catalog(
    catalog: Annotated[CatalogService, Depends(get_catalog)],
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    manager: Annotated[TurnManager, Depends(get_turn_manager)],
) -> CatalogResponse:
    """Hot-reload ``fixtures/catalog.json`` and refresh live scene validators.

    The new catalog is validated against every live scene before it is applied,
    so a reload that removes an in-use kind cannot silently break editing.
    """
    entries = catalog.preview()
    merged = catalog.preview_merged()
    try:
        registry.validate_catalog({entry.kind_id: entry for entry in merged})
    except SceneError as error:
        raise SceneError(
            "invalid_input",
            f"Catalog reload would make a live scene invalid: {error.message}",
            "Keep every kind used by an open scene, or close those scenes first.",
            {"cause": error.code, "details": error.details},
        ) from error
    catalog.apply(entries)
    registry.update_catalog(catalog.by_kind)
    manager.update_catalog(catalog.by_kind)
    return CatalogResponse(entries=catalog.entries)

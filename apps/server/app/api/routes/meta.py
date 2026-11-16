"""Catalog and built-in fixture endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_catalog, get_fixtures
from app.catalog.service import CatalogService
from app.contracts.api import CatalogResponse, FixtureListResponse
from app.scene.fixtures import FixtureService

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

"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import api_router
from app.api.routes.ws import router as ws_router
from app.catalog.service import CatalogService
from app.core.config import Settings, ensure_single_worker, get_settings
from app.core.logging import configure_logging
from app.db import models  # noqa: F401 - register ORM tables on Base.metadata
from app.db.base import Base
from app.db.repository import SceneRepository
from app.db.session import create_engine, create_session_factory
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry
from app.sync.gateway import SyncGateway

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Passing ``settings`` explicitly keeps tests deterministic without touching
    the cached process-wide settings.
    """
    ensure_single_worker()
    app_settings = settings or get_settings()
    configure_logging(
        app_settings.log_level,
        json_logs=app_settings.environment != "development",
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(settings=app_settings)
        if app_settings.environment != "production":
            # Convenience for `pnpm dev` and tests. Production applies the
            # Alembic migration chain explicitly (Docker CMD / python -m app).
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        session_factory = create_session_factory(engine)
        repository = SceneRepository(session_factory)
        catalog = CatalogService()
        fixtures = FixtureService()
        registry = SceneRegistry(repository, catalog=catalog.by_kind)
        gateway = SyncGateway(registry)

        application.state.engine = engine
        application.state.repository = repository
        application.state.catalog = catalog
        application.state.fixtures = fixtures
        application.state.registry = registry
        application.state.gateway = gateway

        logger.info(
            "server.startup",
            version=app_settings.app_version,
            environment=app_settings.environment,
            data_dir=str(app_settings.data_dir),
            catalog_entries=len(catalog.entries),
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("server.shutdown")

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = app_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": app_settings.app_name, "docs": "/api/docs"}

    return app


app = create_app()

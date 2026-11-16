"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Passing ``settings`` explicitly keeps tests deterministic without touching
    the cached process-wide settings.
    """
    app_settings = settings or get_settings()
    configure_logging(
        app_settings.log_level,
        json_logs=app_settings.environment != "development",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "server.startup",
            version=app_settings.app_version,
            environment=app_settings.environment,
            data_dir=str(app_settings.data_dir),
        )
        yield
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
    app.include_router(api_router, prefix="/api")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": app_settings.app_name, "docs": "/api/docs"}

    return app


app = create_app()

"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.llm import LLMAdapter, build_adapter
from app.agent.manager import TurnManager
from app.api.errors import install_error_handlers
from app.api.routes import api_router
from app.api.routes.ws import router as ws_router
from app.capture.service import CaptureProvider, CaptureService
from app.catalog.service import CatalogService
from app.contracts.rules import get_rules
from app.core.config import Settings, ensure_single_worker, get_settings
from app.core.logging import configure_logging
from app.core.paths import find_assets_root
from app.db import models  # noqa: F401 - register ORM tables on Base.metadata
from app.db.base import Base
from app.db.repository import SceneRepository
from app.db.session import create_engine, create_session_factory
from app.scene.fixtures import FixtureService
from app.scene.registry import SceneRegistry
from app.solver.service import SolverService
from app.sync.gateway import SyncGateway
from app.vlm.client import VLMClient, build_vlm_client
from app.vlm.service import VLMService

logger = structlog.get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    llm_adapter: LLMAdapter | None = None,
    vlm_client: VLMClient | None = None,
    capture_provider: CaptureProvider | None = None,
    solver: SolverService | None = None,
) -> FastAPI:
    """Build the ASGI application.

    Passing ``settings`` explicitly keeps tests deterministic without touching
    the cached process-wide settings. Tests may inject a scripted adapter.
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
        solver_service = solver or SolverService(app_settings)
        capture = CaptureService(
            app_settings,
            resolver=lambda scene_id, turn_id, legend: gateway.request_capture(
                scene_id,
                turn_id,
                timeout=app_settings.capture_timeout_seconds,
                legend=legend,
            ),
            provider=capture_provider,
        )
        capture.cleanup()

        async def _capture_cleanup_loop() -> None:
            while True:
                await asyncio.sleep(6 * 3600)
                await asyncio.to_thread(capture.cleanup)

        cleanup_task = asyncio.create_task(_capture_cleanup_loop())
        vlm = None
        resolved_vlm = vlm_client or build_vlm_client(app_settings)
        if resolved_vlm is not None:
            vlm = VLMService(resolved_vlm, app_settings)
        manager = TurnManager(
            registry=registry,
            repository=repository,
            catalog=catalog.by_kind,
            rules=get_rules(),
            llm=llm_adapter or build_adapter(app_settings),
            sink=gateway,
            solver=solver_service,
            capture=capture,
            vlm=vlm,
        )
        gateway.set_manager(manager)
        await manager.restore_after_restart()

        application.state.engine = engine
        application.state.repository = repository
        application.state.catalog = catalog
        application.state.fixtures = fixtures
        application.state.registry = registry
        application.state.gateway = gateway
        application.state.turn_manager = manager
        application.state.solver = solver_service
        application.state.capture = capture
        application.state.vlm = vlm

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
            cleanup_task.cancel()
            await manager.shutdown()
            await solver_service.shutdown()
            if resolved_vlm is not None and hasattr(resolved_vlm, "aclose"):
                await resolved_vlm.aclose()
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
    assets_root = find_assets_root()
    if assets_root is not None:
        app.mount("/assets", StaticFiles(directory=assets_root, check_dir=False), name="assets")
    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": app_settings.app_name, "docs": "/api/docs"}

    return app


app = create_app()

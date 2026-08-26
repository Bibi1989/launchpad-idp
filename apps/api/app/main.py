from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import CorrelationIdMiddleware
from app.routers.api import router as api_router
from app.routers.auth import router as auth_router
from app.routers.dockerfiles import router as dockerfiles_router
from app.routers.orgs import router as orgs_router
from app.routers.projects import router as projects_router
from app.routers.invites import router as invites_router
from app.routers.billing import router as billing_router
from app.routers.provisioning import router as provisioning_router
from app.routers.terminal_ws import router as terminal_ws_router
from app.routers.environment_shell_ws import router as environment_shell_ws_router
from app.routers.webhooks import router as webhooks_router
from app.routers.catalog import router as catalog_router

from app.routers.promotions import router as promotions_router
from app.routers.well_known import router as well_known_router
from app.routers.k8s import router as k8s_router
from app.routers.user_credentials import router as user_credentials_router
from app.routers.imports import router as imports_router
from app.routers.integrations import router as integrations_router
from app.routers.nodes import router as nodes_router
from app.routers.nodes import ws_router as nodes_ws_router
from app.routers.ai_provisioner import router as ai_provisioner_router
from app.routers.cloud_providers import router as cloud_providers_router

configure_logging()
logger = get_logger(__name__)

async def _ttl_reaper_loop(stop: asyncio.Event) -> None:
    """Mark TTL-expired environments EXPIRED in-process so it works without Celery beat."""
    settings = get_settings()
    interval = max(30.0, float(settings.ttl_reaper_interval_seconds))
    while not stop.is_set():
        try:
            from app.workers.tasks import _run_ttl_reaper

            reaped = await _run_ttl_reaper()
            if reaped:
                logger.info("in_process_ttl_reaper", reaped=reaped)
        except Exception as exc:  # noqa: BLE001 - never crash the API loop
            logger.error("in_process_ttl_reaper_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("api_startup", service="launchpad-api")
    stop = asyncio.Event()
    reaper_task = asyncio.create_task(_ttl_reaper_loop(stop), name="ttl-reaper")
    try:
        yield
    finally:
        stop.set()
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        from app.core.events import close_redis

        await close_redis()
        logger.info("api_shutdown", service="launchpad-api")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    # Correlation first (inner), CORS last (outer) so error responses keep ACAO headers.
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.correlation_header],
    )
    register_exception_handlers(app)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(orgs_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(invites_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(promotions_router, prefix="/api/v1")
    app.include_router(provisioning_router, prefix="/api/v1")
    app.include_router(dockerfiles_router, prefix="/api/v1")
    app.include_router(terminal_ws_router, prefix="/api/v1")
    app.include_router(environment_shell_ws_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(k8s_router, prefix="/api/v1")
    app.include_router(user_credentials_router, prefix="/api/v1")
    app.include_router(imports_router, prefix="/api/v1")
    app.include_router(integrations_router, prefix="/api/v1")
    app.include_router(nodes_router, prefix="/api/v1")
    app.include_router(nodes_ws_router, prefix="/api/v1")
    app.include_router(ai_provisioner_router, prefix="/api/v1")
    app.include_router(cloud_providers_router, prefix="/api/v1")
    app.include_router(well_known_router)
    return app



app = create_app()

from __future__ import annotations

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
from app.routers.provisioning import router as provisioning_router
from app.routers.terminal_ws import router as terminal_ws_router
from app.routers.webhooks import router as webhooks_router

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("api_startup", service="launchpad-api")
    yield
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
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(provisioning_router, prefix="/api/v1")
    app.include_router(dockerfiles_router, prefix="/api/v1")
    app.include_router(terminal_ws_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    return app


app = create_app()

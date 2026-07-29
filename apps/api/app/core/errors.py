from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.schemas.environment import ErrorDetail, ErrorResponse

logger = get_logger(__name__)


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            code = str(detail["code"])
            message = str(detail["message"])
            extra: dict[str, object] | None = {
                key: value
                for key, value in detail.items()
                if key not in {"code", "message"}
            } or None
        else:
            code = "http_error"
            message = str(detail)
            extra = None

        logger.warning(
            "http_exception",
            status_code=exc.status_code,
            code=code,
            path=str(request.url.path),
        )
        body = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                correlation_id=_correlation_id(request),
                details=extra,
            )
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("validation_error", path=str(request.url.path), errors=exc.errors())
        details: dict[str, object] = {"errors": list(exc.errors())}
        body = ErrorResponse(
            error=ErrorDetail(
                code="validation_error",
                message="Request validation failed",
                correlation_id=_correlation_id(request),
                details=details,
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=body.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=str(request.url.path))
        body = ErrorResponse(
            error=ErrorDetail(
                code="internal_error",
                message="An unexpected error occurred",
                correlation_id=_correlation_id(request),
            )
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=body.model_dump(),
        )

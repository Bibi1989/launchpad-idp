from __future__ import annotations

import uuid
from typing import MutableMapping

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings


class CorrelationIdMiddleware:
    """Pure ASGI middleware - avoids BaseHTTPMiddleware dropping CORS on errors."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        header_name = settings.correlation_header.lower().encode("latin-1")
        correlation_id = str(uuid.uuid4())

        for key, value in scope.get("headers", []):
            if key == header_name:
                correlation_id = value.decode("latin-1")
                break

        state = scope.setdefault("state", {})
        if isinstance(state, MutableMapping):
            state["correlation_id"] = correlation_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        response_header = (
            settings.correlation_header.encode("latin-1"),
            correlation_id.encode("latin-1"),
        )

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(response_header)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_correlation)

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from app.core.config import get_settings

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization|credential)\s*[:=]\s*\S+"
)


def sanitize_log_message(message: str) -> str:
    return _SENSITIVE_PATTERN.sub(r"\1=[REDACTED]", message)


def _sanitize_event(
    _logger: logging.Logger, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    event = event_dict.get("event")
    if isinstance(event, str):
        event_dict["event"] = sanitize_log_message(event)

    for key, value in list(event_dict.items()):
        if isinstance(value, str) and _SENSITIVE_PATTERN.search(value):
            event_dict[key] = sanitize_log_message(value)
        if key.lower() in {"password", "secret", "token", "api_key", "authorization"}:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _sanitize_event,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

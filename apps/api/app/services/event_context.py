"""``x-launchpad-ctx`` message-header injection for async event publishers.

Carries the active preview/environment context across an async message bus so a
downstream consumer can attribute events to the right Launchpad preview. It is
transport-agnostic: the same context value drops into a Kafka ``ProducerRecord``
header (bytes) or a RabbitMQ ``BasicProperties.headers`` entry (str).

Design goals (per request):
- **Opt-in**: injection only happens when a preview context is present (set via
  :func:`use_preview_context`) or explicitly passed. Pass ``enabled=False`` to
  disable entirely.
- **Graceful fallback**: with no preview context, the injectors return the
  headers unchanged - a non-preview publish is never modified or blocked.

There is no Kafka/RabbitMQ broker wired into this stack yet; this utility is the
piece a publisher would call. Example wiring:

    # RabbitMQ (pika)
    props = pika.BasicProperties(headers=inject_ctx_headers(existing_headers))
    # Kafka (aiokafka / kafka-python)
    await producer.send(topic, value=payload, headers=kafka_ctx_headers())
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass

CTX_HEADER = "x-launchpad-ctx"


@dataclass(frozen=True, slots=True)
class PreviewContext:
    """The subset of Launchpad context worth propagating with an event."""

    environment_id: str | None = None
    workspace_id: str | None = None
    pr_number: int | None = None
    correlation_id: str | None = None
    preview: bool = True

    def is_empty(self) -> bool:
        return not (
            self.environment_id
            or self.workspace_id
            or self.pr_number
            or self.correlation_id
        )

    def to_header_value(self) -> str:
        """Compact, stable JSON (sorted keys) for the header value."""
        data = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(data, separators=(",", ":"), sort_keys=True)


_ctx: ContextVar[PreviewContext | None] = ContextVar("launchpad_preview_ctx", default=None)


def current_preview_context() -> PreviewContext | None:
    return _ctx.get()


def set_preview_context(ctx: PreviewContext | None) -> None:
    """Bind the active preview context (e.g. at the start of a provision task)."""
    _ctx.set(ctx)


@contextlib.contextmanager
def use_preview_context(ctx: PreviewContext | None) -> Iterator[None]:
    """Scope a preview context; resets on exit (safe across asyncio tasks)."""
    token = _ctx.set(ctx)
    try:
        yield
    finally:
        _ctx.reset(token)


def launchpad_ctx_header_value(context: PreviewContext | None = None) -> str | None:
    """The ``x-launchpad-ctx`` value, or None when there is no usable context."""
    ctx = context if context is not None else current_preview_context()
    if ctx is None or ctx.is_empty():
        return None
    return ctx.to_header_value()


def inject_ctx_headers(
    headers: dict[str, str] | None = None,
    *,
    context: PreviewContext | None = None,
    enabled: bool = True,
) -> dict[str, str]:
    """Return a copy of ``headers`` with ``x-launchpad-ctx`` added when applicable.

    Suitable for RabbitMQ ``BasicProperties(headers=...)``. Graceful: with no
    preview context (or ``enabled=False``), returns the headers unchanged.
    """
    result = dict(headers or {})
    if not enabled:
        return result
    value = launchpad_ctx_header_value(context)
    if value is not None:
        result[CTX_HEADER] = value
    return result


def kafka_ctx_headers(
    context: PreviewContext | None = None,
    *,
    enabled: bool = True,
) -> list[tuple[str, bytes]]:
    """Kafka ``ProducerRecord`` header form: ``[(key, value-bytes)]`` (empty if none)."""
    if not enabled:
        return []
    value = launchpad_ctx_header_value(context)
    return [(CTX_HEADER, value.encode("utf-8"))] if value is not None else []


def parse_ctx_header(value: str | bytes | None) -> PreviewContext | None:
    """Consumer side: decode an ``x-launchpad-ctx`` header back to a context."""
    if value is None:
        return None
    raw = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    pr = data.get("pr_number")
    return PreviewContext(
        environment_id=data.get("environment_id"),
        workspace_id=data.get("workspace_id"),
        pr_number=pr if isinstance(pr, int) else None,
        correlation_id=data.get("correlation_id"),
        preview=bool(data.get("preview", True)),
    )

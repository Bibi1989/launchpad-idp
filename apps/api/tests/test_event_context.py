"""x-launchpad-ctx header injection for async event publishers (Kafka/RabbitMQ)."""

from __future__ import annotations

from app.services.event_context import (
    CTX_HEADER,
    PreviewContext,
    inject_ctx_headers,
    kafka_ctx_headers,
    launchpad_ctx_header_value,
    parse_ctx_header,
    use_preview_context,
)


def test_no_context_is_graceful_noop() -> None:
    # No preview context bound and none passed -> headers unchanged, no header added.
    assert launchpad_ctx_header_value() is None
    assert inject_ctx_headers({"content-type": "application/json"}) == {
        "content-type": "application/json"
    }
    assert kafka_ctx_headers() == []


def test_empty_context_is_noop() -> None:
    assert launchpad_ctx_header_value(PreviewContext()) is None
    assert inject_ctx_headers(None, context=PreviewContext()) == {}


def test_inject_adds_header_when_context_present() -> None:
    ctx = PreviewContext(environment_id="env-1", pr_number=42, correlation_id="corr-9")
    headers = inject_ctx_headers({"k": "v"}, context=ctx)
    assert headers["k"] == "v"
    assert CTX_HEADER in headers
    round_trip = parse_ctx_header(headers[CTX_HEADER])
    assert round_trip == ctx


def test_enabled_false_disables_injection() -> None:
    ctx = PreviewContext(environment_id="env-1")
    assert inject_ctx_headers({}, context=ctx, enabled=False) == {}
    assert kafka_ctx_headers(ctx, enabled=False) == []


def test_kafka_headers_are_bytes() -> None:
    ctx = PreviewContext(environment_id="env-1")
    headers = kafka_ctx_headers(ctx)
    assert len(headers) == 1
    key, value = headers[0]
    assert key == CTX_HEADER
    assert isinstance(value, bytes)
    assert parse_ctx_header(value) == ctx


def test_context_var_binding() -> None:
    ctx = PreviewContext(environment_id="env-ctx", workspace_id="ws-1")
    assert launchpad_ctx_header_value() is None
    with use_preview_context(ctx):
        # Injection now picks up the bound context automatically (opt-in via scope).
        headers = inject_ctx_headers({})
        assert parse_ctx_header(headers[CTX_HEADER]) == ctx
    # Reset on exit.
    assert launchpad_ctx_header_value() is None


def test_parse_bad_header_returns_none() -> None:
    assert parse_ctx_header(None) is None
    assert parse_ctx_header("not-json") is None
    assert parse_ctx_header(b"[1,2,3]") is None  # not an object

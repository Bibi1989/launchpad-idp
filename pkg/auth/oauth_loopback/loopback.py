"""Ephemeral 127.0.0.1 HTTP listener for OAuth redirects (RFC 8252)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog

from pkg.auth.oauth_loopback.html_pages import SUCCESS_HTML, render_error_html
from pkg.auth.oauth_loopback.models import (
    AuthCodeResult,
    OAuthLoopbackError,
    OAuthTimeoutError,
)

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_CALLBACK_PATH = "/callback"


class LoopbackServer:
    """Bind ``127.0.0.1`` (ephemeral or fixed port) and capture one OAuth callback."""

    def __init__(
        self,
        *,
        port: int = 0,
        path: str = DEFAULT_CALLBACK_PATH,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        expected_state: str | None = None,
    ) -> None:
        if not path.startswith("/"):
            raise ValueError("callback path must start with '/'")
        self._path = path
        self._timeout_seconds = float(timeout_seconds)
        self._expected_state = expected_state
        self._result: AuthCodeResult | None = None
        self._error: Exception | None = None
        self._done = threading.Event()
        self._httpd = HTTPServer(("127.0.0.1", port), self._make_handler())
        self._httpd.timeout = 1.0
        host, bound_port = self._httpd.server_address[:2]
        self.host = str(host)
        self.port = int(bound_port)
        self.redirect_uri = f"http://{self.host}:{self.port}{self._path}"
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                logger.debug("oauth_loopback_http", message=format % args)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != server._path:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return

                qs = parse_qs(parsed.query)
                result = AuthCodeResult(
                    code=(qs.get("code") or [None])[0],
                    state=(qs.get("state") or [None])[0],
                    error=(qs.get("error") or [None])[0],
                    error_description=(qs.get("error_description") or [None])[0],
                )

                if (
                    server._expected_state is not None
                    and result.state is not None
                    and result.state != server._expected_state
                ):
                    body = render_error_html("Invalid state parameter. Close this window.")
                    self._respond(400, body)
                    server._error = OAuthLoopbackError(
                        "OAuth state mismatch",
                        code="state_mismatch",
                    )
                    server._done.set()
                    return

                if result.error:
                    desc = result.error_description or result.error
                    body = render_error_html(desc)
                    self._respond(400, body)
                    server._result = result
                    server._done.set()
                    return

                if not result.code:
                    body = render_error_html("Missing authorization code.")
                    self._respond(400, body)
                    server._error = OAuthLoopbackError(
                        "Missing authorization code",
                        code="missing_code",
                    )
                    server._done.set()
                    return

                self._respond(200, SUCCESS_HTML)
                server._result = result
                server._done.set()

            def _respond(self, status: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        return Handler

    def start(self) -> None:
        if self._thread is not None:
            return

        def _serve() -> None:
            while not self._done.is_set():
                self._httpd.handle_request()

        self._thread = threading.Thread(
            target=_serve,
            name="oauth-loopback",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "oauth_loopback_listening",
            redirect_uri=self.redirect_uri,
            timeout_seconds=self._timeout_seconds,
        )

    def wait(self) -> AuthCodeResult:
        """Block until callback, error, or timeout. Always shuts down the listener."""
        try:
            finished = self._done.wait(timeout=self._timeout_seconds)
            if not finished:
                raise OAuthTimeoutError()
            if self._error is not None:
                raise self._error
            if self._result is None:
                raise OAuthLoopbackError("No OAuth result captured", code="empty_result")
            return self._result
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._done.set()
        try:
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("oauth_loopback_stopped", redirect_uri=self.redirect_uri)

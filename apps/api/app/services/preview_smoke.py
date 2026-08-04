"""HTTP smoke checks against preview URLs before marking GitHub status green."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SmokeCheckResult:
    ok: bool
    status_code: int | None
    message: str


def run_preview_smoke_check(
    url: str,
    *,
    settings: Settings | None = None,
) -> SmokeCheckResult:
    """GET the preview URL and require a successful HTTP response."""
    cfg = settings or get_settings()
    if not cfg.preview_smoke_enabled:
        return SmokeCheckResult(True, None, "smoke_disabled")
    if not url.startswith(("http://", "https://")):
        return SmokeCheckResult(False, None, "invalid_url")

    timeout = max(1.0, float(cfg.preview_smoke_timeout_seconds))
    req = Request(url, method="GET", headers={"User-Agent": "Launchpad-Preview-Smoke/1.0"})
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - intentional outbound smoke
            code = int(getattr(response, "status", 200) or 200)
            if 200 <= code < 400:
                return SmokeCheckResult(True, code, "ok")
            return SmokeCheckResult(False, code, f"unexpected_status:{code}")
    except HTTPError as exc:
        # Some apps return 401/403 when unauthenticated but are still reachable.
        if exc.code in {401, 403}:
            return SmokeCheckResult(True, exc.code, "reachable_auth_required")
        logger.warning("preview_smoke_http_error", url=url, status=exc.code)
        return SmokeCheckResult(False, exc.code, f"http_error:{exc.code}")
    except URLError as exc:
        logger.warning("preview_smoke_url_error", url=url, error=str(exc.reason))
        return SmokeCheckResult(False, None, f"url_error:{exc.reason}")
    except TimeoutError:
        return SmokeCheckResult(False, None, "timeout")
    except Exception as exc:  # noqa: BLE001 - surface any smoke failure
        logger.warning("preview_smoke_failed", url=url, error=str(exc))
        return SmokeCheckResult(False, None, f"error:{exc}")

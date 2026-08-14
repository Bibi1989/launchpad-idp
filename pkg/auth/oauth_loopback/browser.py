"""Open the system default browser for OAuth authorize URLs."""

from __future__ import annotations

import webbrowser

import structlog

logger = structlog.get_logger(__name__)


def open_authorize_url(url: str, *, new: int = 1) -> bool:
    """Launch the OS default browser.

    Returns True if a browser controller reported success. Callers must still
    tolerate headless environments where this returns False.
    """
    try:
        opened = webbrowser.open(url, new=new, autoraise=True)
    except Exception as exc:  # noqa: BLE001 - surface as soft failure
        logger.warning("oauth_browser_open_failed", error=str(exc))
        return False
    logger.info("oauth_browser_opened", opened=opened)
    return bool(opened)

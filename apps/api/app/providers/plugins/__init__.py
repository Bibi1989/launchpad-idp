"""Built-in provider plugins. Importing this package registers every provider.

Each plugin is optional: a failure to construct/register one must not break the others
or the registry itself. Add a new cloud by dropping a module here and appending a
``register(...)`` line below.
"""

from __future__ import annotations

from app.core.logging import get_logger

from ..registry import register

logger = get_logger(__name__)


def _safe_register(factory) -> None:
    try:
        adapter = factory()
    except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the rest
        logger.warning("provider_plugin_register_failed", error=str(exc)[:300])
        return
    for item in adapter if isinstance(adapter, (list, tuple)) else [adapter]:
        try:
            register(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider_plugin_register_failed", provider=getattr(item, "id", "?"),
                           error=str(exc)[:300])


def _load() -> None:
    from .aws import AWSProvider
    from .azure import AzureProvider
    from .cloudflare import CloudflareProvider
    from .digitalocean import DigitalOceanProvider
    from .gcp import GCPProvider
    from .hetzner import HetznerProvider
    from .legacy_adapter import build_legacy_providers
    from .linode import LinodeProvider
    from .railway import RailwayProvider
    from .render import RenderProvider

    # Native VM / container-host providers.
    _safe_register(HetznerProvider)
    _safe_register(DigitalOceanProvider)
    _safe_register(LinodeProvider)
    _safe_register(AWSProvider)
    _safe_register(GCPProvider)
    _safe_register(AzureProvider)
    # Native PaaS providers.
    _safe_register(RailwayProvider)
    _safe_register(RenderProvider)
    _safe_register(CloudflareProvider)
    # Backward-compat bridges (register under '-legacy' ids).
    _safe_register(build_legacy_providers)


_load()

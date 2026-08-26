"""Provider registry + service catalog.

A process-wide registry maps a string provider id (``"hetzner"``, ``"digitalocean"``,
``"railway"``, ``"legacy"``, ...) to a :class:`CloudProviderAdapter` instance. The catalog
projects each provider's credential/region/tier metadata into a plain, JSON-serializable
structure the frontend can render (which fields to collect, which regions/sizes exist).

Plugins self-register at import time by calling :func:`register`; importing
``app.providers.plugins`` triggers all built-ins.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger

from .base import CloudProviderAdapter, RuntimeTarget

logger = get_logger(__name__)

_REGISTRY: dict[str, CloudProviderAdapter] = {}


def register(adapter: CloudProviderAdapter, *, override: bool = False) -> CloudProviderAdapter:
    """Register a provider adapter under its ``id``. Idempotent unless ``override``."""
    provider_id = (adapter.id or "").strip().lower()
    if not provider_id:
        raise ValueError("provider adapter is missing an 'id'")
    if provider_id in _REGISTRY and not override:
        logger.debug("provider_already_registered", provider=provider_id)
        return _REGISTRY[provider_id]
    _REGISTRY[provider_id] = adapter
    logger.info("provider_registered", provider=provider_id)
    return adapter


def get_provider(provider_id: str) -> CloudProviderAdapter | None:
    _ensure_plugins_loaded()
    return _REGISTRY.get((provider_id or "").strip().lower())


def require_provider(provider_id: str) -> CloudProviderAdapter:
    adapter = get_provider(provider_id)
    if adapter is None:
        raise KeyError(f"unknown provider '{provider_id}'")
    return adapter


def list_providers() -> list[CloudProviderAdapter]:
    _ensure_plugins_loaded()
    return list(_REGISTRY.values())


def build_catalog() -> list[dict[str, Any]]:
    """Return UI-facing metadata for every registered provider."""
    catalog: list[dict[str, Any]] = []
    for adapter in list_providers():
        catalog.append(
            {
                "id": adapter.id,
                "label": adapter.label,
                "docs_url": adapter.docs_url,
                "runtime_targets": [t.value for t in adapter.service_types()],
                "credential_fields": [f.model_dump() for f in adapter.credential_fields()],
                "regions": [r.model_dump() for r in _safe_regions(adapter)],
                "tiers": [t.model_dump() for t in _safe_tiers(adapter)],
            }
        )
    return catalog


def catalog_for(provider_id: str, *, credentials: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return catalog metadata for a single provider, optionally with live regions/tiers."""
    adapter = require_provider(provider_id)
    return {
        "id": adapter.id,
        "label": adapter.label,
        "docs_url": adapter.docs_url,
        "runtime_targets": [t.value for t in adapter.service_types()],
        "credential_fields": [f.model_dump() for f in adapter.credential_fields()],
        "regions": [r.model_dump() for r in _safe_regions(adapter, credentials)],
        "tiers": [t.model_dump() for t in _safe_tiers(adapter, credentials)],
    }


def _safe_regions(adapter: CloudProviderAdapter, credentials: Mapping[str, str] | None = None):
    try:
        return adapter.regions(credentials)
    except Exception as exc:  # noqa: BLE001 - catalog must not fail on one provider
        logger.debug("provider_regions_failed", provider=adapter.id, error=str(exc)[:200])
        return []


def _safe_tiers(adapter: CloudProviderAdapter, credentials: Mapping[str, str] | None = None):
    try:
        return adapter.tiers(credentials)
    except Exception as exc:  # noqa: BLE001
        logger.debug("provider_tiers_failed", provider=adapter.id, error=str(exc)[:200])
        return []


_PLUGINS_LOADED = False


def _ensure_plugins_loaded() -> None:
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True
    try:
        from . import plugins  # noqa: F401 - import side effect registers built-ins
    except Exception as exc:  # noqa: BLE001 - never let plugin import break callers
        logger.warning("provider_plugins_import_failed", error=str(exc)[:300])


__all__ = [
    "RuntimeTarget",
    "build_catalog",
    "catalog_for",
    "get_provider",
    "list_providers",
    "register",
    "require_provider",
]

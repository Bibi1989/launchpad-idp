"""Extensible, plugin-based multi-cloud provisioning engine.

Additive to the existing ``cloud_instance_compute`` path - nothing here changes or
replaces current behavior. Public surface:

* :class:`CloudProviderAdapter` - the interface every cloud plugin implements.
* :func:`get_provider` / :func:`require_provider` / :func:`build_catalog` - registry access.
* :mod:`cloud_init` - VM bootstrap (user-data) generator + health poller.

Built-in plugins (Hetzner, DigitalOcean, Railway, and legacy GCP/AWS/Azure) self-register
on first registry access.
"""

from __future__ import annotations

from .base import (
    CloudProviderAdapter,
    ComputeTier,
    CredentialError,
    CredentialField,
    DeploymentStatus,
    ProviderError,
    ProvisioningError,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
)
from .registry import (
    build_catalog,
    catalog_for,
    get_provider,
    list_providers,
    register,
    require_provider,
)

__all__ = [
    "CloudProviderAdapter",
    "ComputeTier",
    "CredentialError",
    "CredentialField",
    "DeploymentStatus",
    "ProviderError",
    "ProvisionResult",
    "ProvisionSpec",
    "ProvisioningError",
    "RegionOption",
    "RuntimeTarget",
    "StatusResult",
    "build_catalog",
    "catalog_for",
    "get_provider",
    "list_providers",
    "register",
    "require_provider",
]

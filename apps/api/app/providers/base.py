"""Plugin-based multi-cloud provisioning contracts.

This is an ADDITIVE engine. Nothing here replaces the existing
``cloud_instance_compute.provision_cloud_vm`` / ``attach_deploy.deploy_attach`` paths -
they keep working unchanged. This module defines a typed :class:`CloudProviderAdapter`
interface plus JSON-serializable, Postgres-persistable result models and a rollback
helper, so new clouds (Hetzner, DigitalOcean, Railway, ...) can be added as isolated
plugins under ``providers/plugins/`` and surfaced to the UI via the registry/catalog.

Design notes
------------
* Adapters are **synchronous** (like the existing subprocess-based provisioning). Async
  callers wrap them in ``asyncio.to_thread`` - matching how the worker already invokes
  ``provision_cloud_vm`` / ``deploy_attach``.
* Credentials are passed as a plain ``Mapping[str, str]`` (the catalog declares which
  fields each provider needs) so the engine stays decoupled from the frontend-bound
  ``CloudCredentials`` model - no changes to existing schemas are required.
"""

from __future__ import annotations

import abc
import enum
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)


class RuntimeTarget(str, enum.Enum):
    """Where the workload runs once provisioned."""

    VM = "vm"
    DOCKER_HOST = "docker_host"
    KUBERNETES = "kubernetes"
    PAAS = "paas"


class DeploymentStatus(str, enum.Enum):
    """Normalized status across every provider (their raw states map onto these)."""

    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERROR = "error"
    DESTROYED = "destroyed"
    UNKNOWN = "unknown"


# --- Catalog metadata (consumed by the UI) --------------------------------------------


class CredentialField(BaseModel):
    """One credential input the UI must collect for a provider."""

    name: str
    label: str
    secret: bool = True
    required: bool = True
    help: str | None = None
    placeholder: str | None = None


class RegionOption(BaseModel):
    value: str
    label: str


class ComputeTier(BaseModel):
    """A selectable compute size (server type / droplet size / plan)."""

    id: str
    label: str
    vcpus: int | None = None
    memory_mb: int | None = None
    monthly_usd: float | None = None


# --- Provision inputs / outputs -------------------------------------------------------


class ProvisionSpec(BaseModel):
    """Typed, JSON-serializable request describing what to provision."""

    environment_id: str
    runtime_target: RuntimeTarget = RuntimeTarget.DOCKER_HOST
    name: str | None = None
    region: str | None = None
    tier: str | None = None
    # Container image to run (docker host / PaaS). Optional for git-based PaaS.
    image: str | None = None
    app_port: int = 8080
    env_vars: dict[str, str] = Field(default_factory=dict)
    ssh_public_key: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    health_path: str = "/"
    # Git-based PaaS deploys.
    git_repo_url: str | None = None
    git_branch: str | None = None
    # Provider-specific escape hatch (never required).
    extra: dict[str, Any] = Field(default_factory=dict)


class ProvisionResult(BaseModel):
    """Structured provisioning outcome - safe to persist as JSON in PostgreSQL."""

    provider: str
    runtime_target: RuntimeTarget
    resource_id: str
    resource_ids: list[str] = Field(default_factory=list)
    status: DeploymentStatus = DeploymentStatus.PROVISIONING
    ip_address: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    connection_meta: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StatusResult(BaseModel):
    status: DeploymentStatus
    ip_address: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    message: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


# --- Errors ---------------------------------------------------------------------------


class ProviderError(RuntimeError):
    """Base error for the provider engine."""


class CredentialError(ProviderError):
    """Credentials are missing or invalid."""


class ProvisioningError(ProviderError):
    """Provisioning failed. Rollback of partial resources has already been attempted."""

    def __init__(self, message: str, *, partial_resource_ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.partial_resource_ids = list(partial_resource_ids or [])


# --- Rollback tracker -----------------------------------------------------------------


@dataclass
class RollbackTracker:
    """Records created resources + their cleanups so a mid-way failure can roll back."""

    resource_ids: list[str] = field(default_factory=list)
    _cleanups: list[Callable[[], None]] = field(default_factory=list)

    def track(self, resource_id: str, cleanup: Callable[[], None]) -> None:
        self.resource_ids.append(resource_id)
        self._cleanups.append(cleanup)

    def rollback(self) -> None:
        """Best-effort teardown of tracked resources, newest first."""
        for cleanup in reversed(self._cleanups):
            try:
                cleanup()
            except Exception as exc:  # noqa: BLE001 - cleanup must never mask the cause
                logger.warning("provider_rollback_cleanup_failed", error=str(exc)[:300])
        self._cleanups.clear()


@contextmanager
def rollback_on_error(provider: str) -> Iterator[RollbackTracker]:
    """Context manager: on any exception, roll back tracked resources and re-raise as
    :class:`ProvisioningError` carrying the partial resource ids that were cleaned up.
    """
    tracker = RollbackTracker()
    try:
        yield tracker
    except ProvisioningError:
        tracker.rollback()
        raise
    except Exception as exc:
        partial = list(tracker.resource_ids)
        logger.warning(
            "provider_provision_failed_rolling_back",
            provider=provider,
            resources=partial,
            error=str(exc)[:300],
        )
        tracker.rollback()
        raise ProvisioningError(
            f"{provider}: provisioning failed ({exc}); rolled back {len(partial)} resource(s)",
            partial_resource_ids=partial,
        ) from exc


# --- The adapter contract -------------------------------------------------------------


class CloudProviderAdapter(abc.ABC):
    """Every cloud plugin implements this interface.

    Subclasses set the class attributes ``id``, ``label`` and ``runtime_targets`` and
    implement the four lifecycle methods. Catalog metadata methods have safe defaults.
    """

    id: str = ""
    label: str = ""
    runtime_targets: tuple[RuntimeTarget, ...] = ()
    docs_url: str | None = None

    # --- lifecycle ---
    @abc.abstractmethod
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        """Return True when the credentials authenticate against the provider."""

    @abc.abstractmethod
    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        """Create resources for ``environment_id``. Must roll back on partial failure."""

    @abc.abstractmethod
    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        """Return the current status of a previously provisioned resource."""

    @abc.abstractmethod
    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        """Tear down a resource. Idempotent: a missing resource is a success."""

    # --- catalog metadata (overridable) ---
    def credential_fields(self) -> list[CredentialField]:
        return []

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return []

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return []

    def service_types(self) -> list[RuntimeTarget]:
        return list(self.runtime_targets)

    # --- helpers for subclasses ---
    def _require(self, credentials: Mapping[str, str], key: str) -> str:
        value = str(credentials.get(key) or "").strip()
        if not value:
            raise CredentialError(f"{self.label}: missing required credential '{key}'")
        return value

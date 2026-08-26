"""Legacy bridge: expose the existing GCP/AWS/Azure VM engine through the new registry.

This adapter does NOT reimplement anything - it delegates to the existing, battle-tested
``cloud_instance_compute.provision_cloud_vm`` / ``teardown_cloud_vm``. Its purpose is
backward compatibility: the new catalog can list the clouds Launchpad already supports,
and callers that prefer the unified :class:`CloudProviderAdapter` interface can reach them
without a second code path. All heavy imports are lazy so importing the registry never
pulls the legacy stack.

One adapter instance is registered per legacy provider id (``gcp``, ``aws``, ``azure``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger

from ..base import (
    CloudProviderAdapter,
    CredentialField,
    DeploymentStatus,
    ProviderError,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
)

logger = get_logger(__name__)


# Which credential fields (on the existing CloudCredentials model) each provider needs.
_CRED_FIELDS: dict[str, list[CredentialField]] = {
    "gcp": [
        CredentialField(name="gcp_sa_key_json", label="Service Account JSON", secret=True,
                        help="GCP service account key JSON with Compute Admin."),
        CredentialField(name="gcp_project_id", label="GCP Project ID", secret=False, required=True),
        CredentialField(name="gcp_region", label="Region", secret=False, required=False,
                        placeholder="us-central1"),
    ],
    "aws": [
        CredentialField(name="aws_access_key_id", label="Access Key ID", secret=True),
        CredentialField(name="aws_secret_access_key", label="Secret Access Key", secret=True),
        CredentialField(name="aws_region", label="Region", secret=False, required=False,
                        placeholder="us-east-1"),
    ],
    "azure": [
        CredentialField(name="azure_client_id", label="Client ID", secret=True),
        CredentialField(name="azure_client_secret", label="Client Secret", secret=True),
        CredentialField(name="azure_tenant_id", label="Tenant ID", secret=True),
        CredentialField(name="azure_subscription_id", label="Subscription ID", secret=True),
        CredentialField(name="azure_location", label="Location", secret=False, required=False,
                        placeholder="eastus"),
    ],
}

_DEFAULT_REGIONS: dict[str, list[RegionOption]] = {
    "gcp": [RegionOption(value="us-central1", label="us-central1"),
            RegionOption(value="europe-west1", label="europe-west1"),
            RegionOption(value="europe-west3", label="europe-west3")],
    "aws": [RegionOption(value="us-east-1", label="us-east-1"),
            RegionOption(value="eu-west-1", label="eu-west-1")],
    "azure": [RegionOption(value="eastus", label="eastus"),
              RegionOption(value="westeurope", label="westeurope")],
}


class LegacyCloudProvider(CloudProviderAdapter):
    """Bridges one legacy cloud (gcp/aws/azure) to the existing VM engine."""

    runtime_targets = (RuntimeTarget.VM,)

    def __init__(self, provider_id: str, label: str) -> None:
        self.id = provider_id
        self.label = label
        # Metadata tables are keyed by the base cloud id (gcp/aws/azure).
        self._base_id = provider_id.removesuffix("-legacy")

    def credential_fields(self) -> list[CredentialField]:
        return _CRED_FIELDS.get(self._base_id, [])

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return _DEFAULT_REGIONS.get(self._base_id, [])

    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        # Structural check only; the legacy engine performs real auth at provision time.
        required = [f.name for f in self.credential_fields() if f.required and f.secret]
        return all(str(credentials.get(name) or "").strip() for name in required) if required else True

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        from app.schemas.cloud import RunningInstanceConfig, RunningInstanceKind
        from app.services.cloud_instance_compute import provision_cloud_vm

        creds = self._to_credentials(credentials)
        running = RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            service_name=(spec.name or f"lp-{environment_id}")[:63],
            region=spec.region,
            listen_port=spec.app_port,
        )
        try:
            result = provision_cloud_vm(
                running_instance=running,
                environment_id=environment_id,
                environment_name=spec.name or environment_id,
                cloud_provider=self._base_id,
                credentials=creds,
                listen_port=spec.app_port,
                org_slug=(spec.labels or {}).get("org_slug"),
                gcp_project_id=(credentials.get("gcp_project_id") or None),
            )
        except Exception as exc:
            raise ProviderError(f"{self.label}: {exc}") from exc

        host = (result.host or "").strip()
        return ProvisionResult(
            provider=self.id,
            runtime_target=RuntimeTarget.VM,
            resource_id=result.service_name or host or environment_id,
            resource_ids=[v for v in (result.service_name, host) if v],
            status=DeploymentStatus.RUNNING if host else DeploymentStatus.PROVISIONING,
            ip_address=host or None,
            endpoints=[f"http://{host}:{spec.app_port}"] if host else [],
            connection_meta={
                "ssh_user": result.ssh_user,
                "ssh_port": result.ssh_port,
                "ssh_key_path": result.ssh_key_path,
                "service_name": result.service_name,
                "region": result.region,
            },
            metadata={"legacy": True},
        )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        # The legacy engine has no polling API; status is derived by callers from the
        # environment lifecycle. Report UNKNOWN rather than guessing.
        return StatusResult(status=DeploymentStatus.UNKNOWN, message="status tracked by environment lifecycle")

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        from app.schemas.cloud import RunningInstanceConfig, RunningInstanceKind
        from app.services.cloud_instance_compute import teardown_cloud_vm

        creds = self._to_credentials(credentials)
        running = RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            service_name=resource_id[:63],
        )
        try:
            teardown_cloud_vm(
                running_instance=running,
                environment_id=resource_id,
                environment_name=resource_id,
                cloud_provider=self._base_id,
                credentials=creds,
                org_slug=(credentials.get("org_slug") or None),
            )
        except Exception as exc:
            raise ProviderError(f"{self.label}: teardown failed: {exc}") from exc

    def _to_credentials(self, credentials: Mapping[str, str]) -> Any:
        from app.schemas.cloud import CloudCredentials

        known = set(CloudCredentials.model_fields.keys())
        payload = {k: v for k, v in credentials.items() if k in known and v not in (None, "")}
        return CloudCredentials(**payload)


def build_legacy_providers() -> list[LegacyCloudProvider]:
    # Registered under '-legacy' ids so the native aws/gcp/azure plugins own the primary
    # ids while this tested CLI-delegating path stays available as a fallback.
    return [
        LegacyCloudProvider("gcp-legacy", "Google Cloud (VM, legacy engine)"),
        LegacyCloudProvider("aws-legacy", "Amazon Web Services (VM, legacy engine)"),
        LegacyCloudProvider("azure-legacy", "Microsoft Azure (VM, legacy engine)"),
    ]

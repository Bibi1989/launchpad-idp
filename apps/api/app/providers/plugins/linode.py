"""Akamai Linode VM provider (native API + cloud-init).

Boots a Linode instance that runs the app container via cloud-init, delivered through the
Linode Metadata service (``metadata.user_data``, base64). Uses only ``httpx`` against the
Linode API v4. No linode-cli, no Terraform. Idempotent + rollback-safe.

Regions / plans / prices sourced from the Linode API docs (August 2026); Linode advises
using the live API for the authoritative current list.
API reference: https://techdocs.akamai.com/linode-api/reference/api-summary
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.logging import get_logger

from ..base import (
    CloudProviderAdapter,
    ComputeTier,
    CredentialError,
    CredentialField,
    DeploymentStatus,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
    rollback_on_error,
)
from ..cloud_init import render_cloud_init

logger = get_logger(__name__)

_API = "https://api.linode.com/v4"

_STATUS_MAP = {
    "provisioning": DeploymentStatus.PROVISIONING,
    "booting": DeploymentStatus.PROVISIONING,
    "running": DeploymentStatus.RUNNING,
    "offline": DeploymentStatus.DEGRADED,
    "rebooting": DeploymentStatus.DEGRADED,
    "shutting_down": DeploymentStatus.DESTROYED,
    "deleting": DeploymentStatus.DESTROYED,
}


class LinodeProvider(CloudProviderAdapter):
    id = "linode"
    label = "Akamai Linode"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://techdocs.akamai.com/linode-api/reference/api-summary"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_token",
                label="Personal Access Token",
                secret=True,
                required=True,
                help="Linode PAT with Linodes read/write scope (Cloud Manager > API Tokens).",
                placeholder="linode-personal-access-token",
            )
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="us-east", label="Newark, NJ (us-east)"),
            RegionOption(value="us-central", label="Dallas, TX (us-central)"),
            RegionOption(value="us-ord", label="Chicago, IL (us-ord)"),
            RegionOption(value="us-west", label="Fremont, CA (us-west)"),
            RegionOption(value="fr-par", label="Paris (fr-par)"),
            RegionOption(value="eu-west", label="London (eu-west)"),
            RegionOption(value="eu-central", label="Frankfurt (eu-central)"),
            RegionOption(value="ap-south", label="Singapore (ap-south)"),
            RegionOption(value="ap-northeast", label="Tokyo (ap-northeast)"),
        ]

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="g6-nanode-1", label="Nanode 1GB - 1 vCPU / 1 GB", vcpus=1, memory_mb=1024, monthly_usd=5.0),
            ComputeTier(id="g6-standard-1", label="Linode 2GB - 1 vCPU / 2 GB", vcpus=1, memory_mb=2048, monthly_usd=10.0),
            ComputeTier(id="g6-standard-2", label="Linode 4GB - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096, monthly_usd=20.0),
            ComputeTier(id="g6-standard-4", label="Linode 8GB - 4 vCPU / 8 GB", vcpus=4, memory_mb=8192, monthly_usd=40.0),
            ComputeTier(id="g6-standard-6", label="Linode 16GB - 6 vCPU / 16 GB", vcpus=6, memory_mb=16384, monthly_usd=80.0),
            ComputeTier(id="g6-dedicated-2", label="Dedicated 4GB - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096, monthly_usd=30.0),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        token = self._require(credentials, "api_token")
        try:
            self._get(token, "/profile")
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return False
            raise

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        token = self._require(credentials, "api_token")
        if not spec.image:
            raise CredentialError("Linode VM provider requires spec.image (a container image)")

        instance_type = spec.tier or "g6-nanode-1"
        region = spec.region or "us-east"
        label = (spec.name or f"lp-{environment_id}")[:64].replace("_", "-")
        user_data = render_cloud_init(
            image=spec.image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )

        with rollback_on_error(self.label) as tracker:
            payload: dict[str, Any] = {
                "region": region,
                "type": instance_type,
                "image": spec.extra.get("os_image", "linode/ubuntu22.04"),
                "label": label,
                "booted": True,
                "metadata": {"user_data": base64.b64encode(user_data.encode()).decode()},
                "tags": ["launchpad", environment_id[:50]],
            }
            if spec.ssh_public_key:
                payload["authorized_keys"] = [spec.ssh_public_key]
            created = self._post(token, "/linode/instances", payload)
            instance_id = str(created["id"])
            tracker.track(instance_id, lambda iid=instance_id: self._delete_instance(token, iid))

            ipv4 = (created.get("ipv4") or [None])[0]
            status = _STATUS_MAP.get(created.get("status", "provisioning"), DeploymentStatus.PROVISIONING)
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=instance_id,
                resource_ids=[instance_id],
                status=status,
                ip_address=ipv4,
                endpoints=[f"http://{ipv4}:{spec.app_port}"] if ipv4 else [],
                connection_meta={"ssh_user": "root", "ssh_port": 22, "app_port": spec.app_port,
                                 "region": region},
                tags={"launchpad-environment": environment_id},
                metadata={"type": instance_type, "region": region, "label": label},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._require(credentials, "api_token")
        try:
            data = self._get(token, f"/linode/instances/{resource_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="instance not found")
            raise
        ipv4 = (data.get("ipv4") or [None])[0]
        return StatusResult(
            status=_STATUS_MAP.get(data.get("status", ""), DeploymentStatus.UNKNOWN),
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"status": data.get("status")},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token = self._require(credentials, "api_token")
        self._delete_instance(token, resource_id)

    # --- HTTP helpers ---
    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, token: str, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_API}{path}", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def _post(self, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{_API}{path}", headers=self._headers(token), json=payload)
            resp.raise_for_status()
            return resp.json()

    def _delete_instance(self, token: str, instance_id: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(f"{_API}/linode/instances/{instance_id}", headers=self._headers(token))
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

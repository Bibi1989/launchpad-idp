"""Hetzner Cloud VM provider (native HTTP API + cloud-init).

Provisions a Hetzner Cloud server that boots our container app via cloud-init user-data.
Uses only ``httpx`` (already a dependency) against the Hetzner Cloud REST API - no
``hcloud`` SDK, no Terraform, no Ansible. Idempotent + rollback-safe.

API reference: https://docs.hetzner.cloud/
"""

from __future__ import annotations

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

_API = "https://api.hetzner.cloud/v1"
_LABEL_KEY = "launchpad-environment"

# Hetzner server status -> normalized status.
_STATUS_MAP = {
    "initializing": DeploymentStatus.PROVISIONING,
    "starting": DeploymentStatus.PROVISIONING,
    "running": DeploymentStatus.RUNNING,
    "stopping": DeploymentStatus.DEGRADED,
    "off": DeploymentStatus.DEGRADED,
    "deleting": DeploymentStatus.DESTROYED,
    "migrating": DeploymentStatus.DEGRADED,
    "rebuilding": DeploymentStatus.PROVISIONING,
    "unknown": DeploymentStatus.UNKNOWN,
}


class HetznerProvider(CloudProviderAdapter):
    id = "hetzner"
    label = "Hetzner Cloud"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://docs.hetzner.cloud/"

    # --- catalog metadata ---
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_token",
                label="API Token",
                secret=True,
                required=True,
                help="Project API token from Hetzner Cloud Console (Security > API Tokens).",
                placeholder="hetzner-cloud-api-token",
            )
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        # Static fallback avoids an API call for the picker; overridden live when a
        # token is present.
        static = [
            RegionOption(value="nbg1", label="Nuremberg (nbg1)"),
            RegionOption(value="fsn1", label="Falkenstein (fsn1)"),
            RegionOption(value="hel1", label="Helsinki (hel1)"),
            RegionOption(value="ash", label="Ashburn, VA (ash)"),
            RegionOption(value="hil", label="Hillsboro, OR (hil)"),
        ]
        token = str((credentials or {}).get("api_token") or "").strip()
        if not token:
            return static
        try:
            data = self._get(token, "/locations")
            return [
                RegionOption(value=loc["name"], label=f"{loc.get('city', loc['name'])} ({loc['name']})")
                for loc in data.get("locations", [])
            ] or static
        except Exception:  # noqa: BLE001
            return static

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="cx22", label="CX22 - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096, monthly_usd=4.5),
            ComputeTier(id="cx32", label="CX32 - 4 vCPU / 8 GB", vcpus=4, memory_mb=8192, monthly_usd=8.0),
            ComputeTier(id="cpx11", label="CPX11 - 2 vCPU / 2 GB", vcpus=2, memory_mb=2048, monthly_usd=4.0),
            ComputeTier(id="cpx21", label="CPX21 - 3 vCPU / 4 GB", vcpus=3, memory_mb=4096, monthly_usd=7.0),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        token = self._require(credentials, "api_token")
        try:
            self._get(token, "/servers", params={"per_page": 1})
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
        image = spec.image
        if not image:
            raise CredentialError("Hetzner VM provider requires spec.image (a container image)")

        server_type = spec.tier or "cx22"
        location = spec.region or "nbg1"
        name = (spec.name or f"lp-{environment_id}")[:63].lower().replace("_", "-")
        user_data = render_cloud_init(
            image=image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )
        labels = {_LABEL_KEY: environment_id}
        labels.update({k: v for k, v in (spec.labels or {}).items()})

        with rollback_on_error(self.label) as tracker:
            payload: dict[str, Any] = {
                "name": name,
                "server_type": server_type,
                "location": location,
                "image": spec.extra.get("os_image", "docker-ce"),
                "user_data": user_data,
                "labels": labels,
                "public_net": {"enable_ipv4": True, "enable_ipv6": True},
            }
            created = self._post(token, "/servers", payload)
            server = created["server"]
            server_id = str(server["id"])
            tracker.track(server_id, lambda sid=server_id: self._delete_server(token, sid))

            ipv4 = (server.get("public_net", {}).get("ipv4") or {}).get("ip")
            status = _STATUS_MAP.get(server.get("status", "unknown"), DeploymentStatus.PROVISIONING)
            endpoints = [f"http://{ipv4}:{spec.app_port}"] if ipv4 else []
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=server_id,
                resource_ids=[server_id],
                status=status,
                ip_address=ipv4,
                endpoints=endpoints,
                connection_meta={"ssh_user": "root", "ssh_port": 22, "app_port": spec.app_port},
                tags=labels,
                metadata={"server_type": server_type, "location": location, "name": name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._require(credentials, "api_token")
        try:
            data = self._get(token, f"/servers/{resource_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="server not found")
            raise
        server = data["server"]
        ipv4 = (server.get("public_net", {}).get("ipv4") or {}).get("ip")
        status = _STATUS_MAP.get(server.get("status", "unknown"), DeploymentStatus.UNKNOWN)
        return StatusResult(
            status=status,
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"status": server.get("status")},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token = self._require(credentials, "api_token")
        self._delete_server(token, resource_id)

    # --- HTTP helpers ---
    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, token: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_API}{path}", headers=self._headers(token), params=params)
            resp.raise_for_status()
            return resp.json()

    def _post(self, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{_API}{path}", headers=self._headers(token), json=payload)
            resp.raise_for_status()
            return resp.json()

    def _delete_server(self, token: str, server_id: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(f"{_API}/servers/{server_id}", headers=self._headers(token))
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

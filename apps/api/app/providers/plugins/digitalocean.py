"""DigitalOcean Droplet provider (native HTTP API + cloud-init).

Provisions a Droplet that boots our container app via cloud-init user-data. Uses only
``httpx`` against the DigitalOcean v2 REST API - no ``python-digitalocean`` SDK, no
Terraform/Ansible. Idempotent + rollback-safe.

API reference: https://docs.digitalocean.com/reference/api/
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

_API = "https://api.digitalocean.com/v2"
_TAG_PREFIX = "launchpad-env-"

_STATUS_MAP = {
    "new": DeploymentStatus.PROVISIONING,
    "active": DeploymentStatus.RUNNING,
    "off": DeploymentStatus.DEGRADED,
    "archive": DeploymentStatus.DESTROYED,
}


class DigitalOceanProvider(CloudProviderAdapter):
    id = "digitalocean"
    label = "DigitalOcean"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://docs.digitalocean.com/reference/api/"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_token",
                label="API Token",
                secret=True,
                required=True,
                help="Personal access token with read+write scope (API > Tokens).",
                placeholder="dop_v1_...",
            )
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        static = [
            RegionOption(value="nyc1", label="New York 1 (nyc1)"),
            RegionOption(value="nyc3", label="New York 3 (nyc3)"),
            RegionOption(value="sfo3", label="San Francisco 3 (sfo3)"),
            RegionOption(value="ams3", label="Amsterdam 3 (ams3)"),
            RegionOption(value="fra1", label="Frankfurt 1 (fra1)"),
            RegionOption(value="lon1", label="London 1 (lon1)"),
            RegionOption(value="sgp1", label="Singapore 1 (sgp1)"),
        ]
        token = str((credentials or {}).get("api_token") or "").strip()
        if not token:
            return static
        try:
            data = self._get(token, "/regions")
            live = [
                RegionOption(value=r["slug"], label=f"{r.get('name', r['slug'])} ({r['slug']})")
                for r in data.get("regions", [])
                if r.get("available", True)
            ]
            return live or static
        except Exception:  # noqa: BLE001
            return static

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="s-1vcpu-1gb", label="Basic - 1 vCPU / 1 GB", vcpus=1, memory_mb=1024, monthly_usd=6.0),
            ComputeTier(id="s-1vcpu-2gb", label="Basic - 1 vCPU / 2 GB", vcpus=1, memory_mb=2048, monthly_usd=12.0),
            ComputeTier(id="s-2vcpu-2gb", label="Basic - 2 vCPU / 2 GB", vcpus=2, memory_mb=2048, monthly_usd=18.0),
            ComputeTier(id="s-2vcpu-4gb", label="Basic - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096, monthly_usd=24.0),
        ]

    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        token = self._require(credentials, "api_token")
        try:
            self._get(token, "/account")
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
            raise CredentialError("DigitalOcean VM provider requires spec.image (a container image)")

        size = spec.tier or "s-1vcpu-1gb"
        region = spec.region or "nyc3"
        name = (spec.name or f"lp-{environment_id}")[:63].lower().replace("_", "-")
        user_data = render_cloud_init(
            image=spec.image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )
        tag = f"{_TAG_PREFIX}{environment_id}"

        ssh_key_ids = self._resolve_ssh_key_ids(token, spec)

        with rollback_on_error(self.label) as tracker:
            payload: dict[str, Any] = {
                "name": name,
                "region": region,
                "size": size,
                # docker-20-04 marketplace image ships Docker preinstalled; cloud-init
                # still self-heals if the base image lacks it.
                "image": spec.extra.get("os_image", "docker-20-04"),
                "user_data": user_data,
                "tags": [tag],
                "monitoring": True,
            }
            if ssh_key_ids:
                payload["ssh_keys"] = ssh_key_ids

            created = self._post(token, "/droplets", payload)
            droplet = created["droplet"]
            droplet_id = str(droplet["id"])
            tracker.track(droplet_id, lambda did=droplet_id: self._delete_droplet(token, did))

            ipv4 = self._extract_ipv4(droplet)
            status = _STATUS_MAP.get(droplet.get("status", "new"), DeploymentStatus.PROVISIONING)
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=droplet_id,
                resource_ids=[droplet_id],
                status=status,
                ip_address=ipv4,
                endpoints=[f"http://{ipv4}:{spec.app_port}"] if ipv4 else [],
                connection_meta={"ssh_user": "root", "ssh_port": 22, "app_port": spec.app_port},
                tags={"tag": tag},
                metadata={"size": size, "region": region, "name": name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._require(credentials, "api_token")
        try:
            data = self._get(token, f"/droplets/{resource_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="droplet not found")
            raise
        droplet = data["droplet"]
        ipv4 = self._extract_ipv4(droplet)
        status = _STATUS_MAP.get(droplet.get("status", "new"), DeploymentStatus.UNKNOWN)
        return StatusResult(
            status=status,
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"status": droplet.get("status")},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token = self._require(credentials, "api_token")
        self._delete_droplet(token, resource_id)

    # --- helpers ---
    def _resolve_ssh_key_ids(self, token: str, spec: ProvisionSpec) -> list[Any]:
        explicit = spec.extra.get("ssh_key_ids")
        if explicit:
            return list(explicit)
        # cloud-init injects the public key too; registering it as an account key is
        # best-effort and non-fatal.
        if spec.ssh_public_key:
            try:
                res = self._post(
                    token,
                    "/account/keys",
                    {"name": f"lp-{spec.environment_id}", "public_key": spec.ssh_public_key},
                )
                return [res["ssh_key"]["id"]]
            except Exception:  # noqa: BLE001 - already covered by user_data
                return []
        return []

    @staticmethod
    def _extract_ipv4(droplet: dict[str, Any]) -> str | None:
        for net in (droplet.get("networks", {}) or {}).get("v4", []) or []:
            if net.get("type") == "public":
                return net.get("ip_address")
        return None

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

    def _delete_droplet(self, token: str, droplet_id: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(f"{_API}/droplets/{droplet_id}", headers=self._headers(token))
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

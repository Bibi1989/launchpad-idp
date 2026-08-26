"""Google Cloud Compute Engine VM provider (native REST + cloud-init).

Boots a GCE instance that runs the app container via cloud-init (delivered through the
``user-data`` metadata key, which cloud-init on Ubuntu images reads). Auth uses
``google.oauth2.service_account`` to mint a bearer token from a service-account key JSON;
all API calls go through ``httpx`` against the Compute REST API. No gcloud CLI, no
Terraform, no Ansible. Idempotent + rollback-safe (firewall rule + instance).

API reference: https://cloud.google.com/compute/docs/reference/rest/v1
"""

from __future__ import annotations

import json
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
    ProviderError,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
    rollback_on_error,
)
from ..cloud_init import render_cloud_init

logger = get_logger(__name__)

_COMPUTE = "https://compute.googleapis.com/compute/v1"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_STATUS_MAP = {
    "PROVISIONING": DeploymentStatus.PROVISIONING,
    "STAGING": DeploymentStatus.PROVISIONING,
    "RUNNING": DeploymentStatus.RUNNING,
    "STOPPING": DeploymentStatus.DEGRADED,
    "SUSPENDED": DeploymentStatus.DEGRADED,
    "TERMINATED": DeploymentStatus.DESTROYED,
}


class GCPProvider(CloudProviderAdapter):
    id = "gcp"
    label = "Google Cloud (Compute Engine)"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://cloud.google.com/compute/docs/reference/rest/v1"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(name="gcp_sa_key_json", label="Service Account JSON", secret=True,
                            help="Key JSON for a service account with Compute Admin."),
            CredentialField(name="gcp_project_id", label="Project ID", secret=False, required=False,
                            help="Defaults to the project inside the key JSON."),
            CredentialField(name="gcp_region", label="Region", secret=False, required=False,
                            placeholder="us-central1"),
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="us-central1", label="Iowa (us-central1)"),
            RegionOption(value="us-east1", label="South Carolina (us-east1)"),
            RegionOption(value="us-west1", label="Oregon (us-west1)"),
            RegionOption(value="europe-west1", label="Belgium (europe-west1)"),
            RegionOption(value="europe-west3", label="Frankfurt (europe-west3)"),
            RegionOption(value="europe-west4", label="Netherlands (europe-west4)"),
            RegionOption(value="asia-southeast1", label="Singapore (asia-southeast1)"),
        ]

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="e2-small", label="e2-small - 2 vCPU / 2 GB", vcpus=2, memory_mb=2048),
            ComputeTier(id="e2-medium", label="e2-medium - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096),
            ComputeTier(id="e2-standard-2", label="e2-standard-2 - 2 vCPU / 8 GB", vcpus=2, memory_mb=8192),
            ComputeTier(id="e2-standard-4", label="e2-standard-4 - 4 vCPU / 16 GB", vcpus=4, memory_mb=16384),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        try:
            token, project = self._token_and_project(credentials)
            self._get(token, f"/projects/{project}/zones")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("gcp_validate_failed", error=str(exc)[:200])
            return False

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        if not spec.image:
            raise CredentialError("GCP VM provider requires spec.image (a container image)")
        token, project = self._token_and_project(credentials)
        region = spec.region or credentials.get("gcp_region") or "us-central1"
        zone = spec.extra.get("zone") or f"{region}-a"
        machine_type = spec.tier or "e2-small"
        name = self._safe_name(spec.name or f"lp-{environment_id}")
        user_data = render_cloud_init(
            image=spec.image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )
        network_tag = f"lp-{environment_id}"[:63].lower().replace("_", "-")

        with rollback_on_error(self.label) as tracker:
            fw_name = self._ensure_firewall(token, project, network_tag, spec.app_port)
            tracker.track(fw_name, lambda fn=fw_name: self._delete_firewall(token, project, fn))

            body: dict[str, Any] = {
                "name": name,
                "machineType": f"zones/{zone}/machineTypes/{machine_type}",
                "disks": [
                    {
                        "boot": True,
                        "autoDelete": True,
                        "initializeParams": {
                            "sourceImage": spec.extra.get(
                                "source_image",
                                "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts",
                            )
                        },
                    }
                ],
                "networkInterfaces": [
                    {
                        "network": "global/networks/default",
                        "accessConfigs": [{"type": "ONE_TO_ONE_NAT", "name": "External NAT"}],
                    }
                ],
                "metadata": {"items": [{"key": "user-data", "value": user_data}]},
                "labels": {"launchpad-environment": environment_id[:63].lower()},
                "tags": {"items": [network_tag]},
            }
            self._post(token, f"/projects/{project}/zones/{zone}/instances", body)
            tracker.track(name, lambda n=name: self._delete_instance(token, project, zone, n))

            ipv4 = self._instance_ip(token, project, zone, name)
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=f"{zone}/{name}",
                resource_ids=[f"{zone}/{name}", fw_name],
                status=DeploymentStatus.PROVISIONING,
                ip_address=ipv4,
                endpoints=[f"http://{ipv4}:{spec.app_port}"] if ipv4 else [],
                connection_meta={"ssh_user": "ubuntu", "ssh_port": 22, "app_port": spec.app_port,
                                 "zone": zone, "project": project, "firewall": fw_name},
                tags={"launchpad-environment": environment_id},
                metadata={"machine_type": machine_type, "zone": zone, "name": name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token, project = self._token_and_project(credentials)
        zone, name = self._split_resource(resource_id)
        try:
            instance = self._get(token, f"/projects/{project}/zones/{zone}/instances/{name}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="instance not found")
            raise
        ipv4 = self._extract_ip(instance)
        return StatusResult(
            status=_STATUS_MAP.get(instance.get("status", ""), DeploymentStatus.UNKNOWN),
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"status": instance.get("status")},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token, project = self._token_and_project(credentials)
        zone, name = self._split_resource(resource_id)
        self._delete_instance(token, project, zone, name)

    # --- helpers ---
    def _token_and_project(self, credentials: Mapping[str, str]) -> tuple[str, str]:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        raw = str(credentials.get("gcp_sa_key_json") or "").strip()
        if not raw:
            raise CredentialError("GCP provider requires gcp_sa_key_json")
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CredentialError(f"GCP service account JSON is not valid JSON: {exc}") from exc
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        creds.refresh(Request())
        project = str(credentials.get("gcp_project_id") or info.get("project_id") or "").strip()
        if not project:
            raise CredentialError("GCP project id is not set (gcp_project_id or key JSON project_id)")
        return creds.token, project

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
        cleaned = cleaned.strip("-")[:63] or "lp-instance"
        return cleaned if cleaned[0].isalpha() else f"lp-{cleaned}"[:63]

    @staticmethod
    def _split_resource(resource_id: str) -> tuple[str, str]:
        if "/" in resource_id:
            zone, name = resource_id.split("/", 1)
            return zone, name
        raise ProviderError(f"GCP resource id must be 'zone/name', got '{resource_id}'")

    def _ensure_firewall(self, token: str, project: str, network_tag: str, app_port: int) -> str:
        fw_name = f"launchpad-{network_tag}"[:63]
        body = {
            "name": fw_name,
            "network": "global/networks/default",
            "direction": "INGRESS",
            "targetTags": [network_tag],
            "allowed": [{"IPProtocol": "tcp", "ports": [str(app_port), "22"]}],
            "sourceRanges": ["0.0.0.0/0"],
        }
        try:
            self._post(token, f"/projects/{project}/global/firewalls", body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:  # already exists is fine
                raise
        return fw_name

    def _instance_ip(self, token: str, project: str, zone: str, name: str) -> str | None:
        try:
            instance = self._get(token, f"/projects/{project}/zones/{zone}/instances/{name}")
            return self._extract_ip(instance)
        except Exception:  # noqa: BLE001 - IP not assigned yet
            return None

    @staticmethod
    def _extract_ip(instance: dict[str, Any]) -> str | None:
        for nic in instance.get("networkInterfaces", []) or []:
            for cfg in nic.get("accessConfigs", []) or []:
                if cfg.get("natIP"):
                    return cfg["natIP"]
        return None

    def _delete_instance(self, token: str, project: str, zone: str, name: str) -> None:
        self._delete(token, f"/projects/{project}/zones/{zone}/instances/{name}")

    def _delete_firewall(self, token: str, project: str, fw_name: str) -> None:
        self._delete(token, f"/projects/{project}/global/firewalls/{fw_name}")

    # --- HTTP ---
    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, token: str, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_COMPUTE}{path}", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def _post(self, token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{_COMPUTE}{path}", headers=self._headers(token), json=body)
            resp.raise_for_status()
            return resp.json()

    def _delete(self, token: str, path: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(f"{_COMPUTE}{path}", headers=self._headers(token))
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

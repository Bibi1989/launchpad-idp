"""Render PaaS provider (native REST API).

Deploys a container image (or git repo) as a Render Web Service. Render is a managed
platform, not a VM host, so cloud-init does not apply here. Uses only ``httpx`` against
the Render API v1. Idempotent + rollback-safe: the created service is deleted on failure.

Regions / instance plans / prices sourced from the Render docs + pricing (August 2026).
API reference: https://render.com/docs/api
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
    ProviderError,
    ProvisionResult,
    ProvisionSpec,
    RegionOption,
    RuntimeTarget,
    StatusResult,
    rollback_on_error,
)

logger = get_logger(__name__)

_API = "https://api.render.com/v1"


class RenderProvider(CloudProviderAdapter):
    id = "render"
    label = "Render"
    runtime_targets = (RuntimeTarget.PAAS,)
    docs_url = "https://render.com/docs/api"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_key",
                label="API Key",
                secret=True,
                required=True,
                help="Render API key (Account Settings > API Keys).",
                placeholder="rnd_...",
            ),
            CredentialField(
                name="owner_id",
                label="Owner ID",
                secret=False,
                required=False,
                help="Workspace/owner id (usr-... or tea-...); the first owner is used if omitted.",
            ),
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="oregon", label="Oregon, USA (oregon)"),
            RegionOption(value="ohio", label="Ohio, USA (ohio)"),
            RegionOption(value="virginia", label="Virginia, USA (virginia)"),
            RegionOption(value="frankfurt", label="Frankfurt, Germany (frankfurt)"),
            RegionOption(value="singapore", label="Singapore (singapore)"),
        ]

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="starter", label="Starter - 0.5 CPU / 512 MB", memory_mb=512, monthly_usd=7.0),
            ComputeTier(id="standard", label="Standard - 1 CPU / 2 GB", vcpus=1, memory_mb=2048, monthly_usd=25.0),
            ComputeTier(id="pro", label="Pro - 2 CPU / 4 GB", vcpus=2, memory_mb=4096, monthly_usd=85.0),
            ComputeTier(id="pro_plus", label="Pro Plus - 4 CPU / 8 GB", vcpus=4, memory_mb=8192, monthly_usd=175.0),
            ComputeTier(id="pro_max", label="Pro Max - 4 CPU / 16 GB", vcpus=4, memory_mb=16384, monthly_usd=225.0),
            ComputeTier(id="pro_ultra", label="Pro Ultra - 8 CPU / 32 GB", vcpus=8, memory_mb=32768, monthly_usd=450.0),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        key = self._require(credentials, "api_key")
        try:
            self._get(key, "/owners", params={"limit": 1})
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
        key = self._require(credentials, "api_key")
        if not spec.image and not spec.git_repo_url:
            raise CredentialError("Render provider requires spec.image or spec.git_repo_url")

        owner_id = str(credentials.get("owner_id") or "").strip() or self._first_owner(key)
        if not owner_id:
            raise CredentialError("Render: no owner id available; set owner_id")

        name = (spec.name or f"lp-{environment_id}")[:60]
        plan = spec.tier or "starter"
        region = spec.region or "oregon"
        env_vars = [{"key": k, "value": v} for k, v in (spec.env_vars or {}).items()]

        service_details: dict[str, Any] = {
            "env": "image" if spec.image else "docker",
            "plan": plan,
            "region": region,
            "envSpecificDetails": {},
        }
        payload: dict[str, Any] = {
            "type": "web_service",
            "name": name,
            "ownerId": owner_id,
            "serviceDetails": service_details,
            "envVars": env_vars,
        }
        if spec.image:
            payload["image"] = {"ownerId": owner_id, "imagePath": spec.image}
        else:
            payload["repo"] = spec.git_repo_url
            if spec.git_branch:
                payload["branch"] = spec.git_branch

        with rollback_on_error(self.label) as tracker:
            created = self._post(key, "/services", payload)
            service = created.get("service", created)
            service_id = service["id"]
            tracker.track(service_id, lambda sid=service_id: self._delete_service(key, sid))

            url = service.get("serviceDetails", {}).get("url") or service.get("url")
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.PAAS,
                resource_id=service_id,
                resource_ids=[service_id],
                status=DeploymentStatus.PROVISIONING,
                endpoints=[url] if url else [],
                connection_meta={"owner_id": owner_id, "region": region, "plan": plan},
                metadata={"name": name},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        key = self._require(credentials, "api_key")
        try:
            service = self._get(key, f"/services/{resource_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="service not found")
            raise
        suspended = service.get("suspended") == "suspended"
        url = service.get("serviceDetails", {}).get("url") or service.get("url")
        return StatusResult(
            status=DeploymentStatus.DEGRADED if suspended else DeploymentStatus.RUNNING,
            endpoints=[url] if url else [],
            raw={"suspended": service.get("suspended")},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        key = self._require(credentials, "api_key")
        self._delete_service(key, resource_id)

    # --- helpers ---
    def _first_owner(self, key: str) -> str | None:
        data = self._get(key, "/owners", params={"limit": 1})
        if isinstance(data, list) and data:
            owner = data[0].get("owner", data[0])
            return owner.get("id")
        return None

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _get(self, key: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_API}{path}", headers=self._headers(key), params=params)
            resp.raise_for_status()
            return resp.json()

    def _post(self, key: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{_API}{path}", headers=self._headers(key), json=payload)
        if resp.status_code >= 400:
            raise ProviderError(f"Render API {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _delete_service(self, key: str, service_id: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(f"{_API}/services/{service_id}", headers=self._headers(key))
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

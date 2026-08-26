"""Cloudflare Workers provider (native REST).

IMPORTANT: Cloudflare is not a container-VM host. Unlike the VM providers, it cannot run
an arbitrary Docker image. This adapter targets Cloudflare's real product - Workers - and
deploys a Worker script (edge JavaScript). Provide the script via ``spec.extra["worker_script"]``;
``spec.env_vars`` become plain-text Worker bindings. For container workloads, use a VM
provider (Hetzner/DigitalOcean/AWS/GCP/Azure) instead.

Uses only ``httpx`` against the Cloudflare v4 API. No wrangler CLI. Idempotent +
rollback-safe (the script is deleted if a follow-up step fails).

API reference: https://developers.cloudflare.com/api/
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.logging import get_logger

from ..base import (
    CloudProviderAdapter,
    CredentialError,
    CredentialField,
    DeploymentStatus,
    ProviderError,
    ProvisionResult,
    ProvisionSpec,
    RuntimeTarget,
    StatusResult,
    rollback_on_error,
)

logger = get_logger(__name__)

_API = "https://api.cloudflare.com/client/v4"

_PLACEHOLDER_WORKER = (
    "addEventListener('fetch', (event) => {\n"
    "  event.respondWith(new Response('Launchpad Cloudflare Worker is live.', "
    "{ headers: { 'content-type': 'text/plain' } }));\n"
    "});\n"
)


class CloudflareProvider(CloudProviderAdapter):
    id = "cloudflare"
    label = "Cloudflare Workers"
    runtime_targets = (RuntimeTarget.PAAS,)
    docs_url = "https://developers.cloudflare.com/workers/"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                name="cloudflare_api_token",
                label="API Token",
                secret=True,
                required=True,
                help="Token with Workers Scripts:Edit permission.",
            ),
            CredentialField(
                name="cloudflare_account_id",
                label="Account ID",
                secret=False,
                required=False,
                help="Optional; the first accessible account is used when omitted.",
            ),
        ]

    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        token = str(credentials.get("cloudflare_api_token") or "").strip()
        if not token:
            return False
        try:
            data = self._get(token, "/user/tokens/verify")
            return bool(data.get("success"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloudflare_validate_failed", error=str(exc)[:200])
            return False

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        token = self._require(credentials, "cloudflare_api_token")
        account_id = str(credentials.get("cloudflare_account_id") or "").strip() or self._first_account(token)
        if not account_id:
            raise CredentialError("Cloudflare: no accessible account; set cloudflare_account_id")

        script_name = self._safe_name(spec.name or f"lp-{environment_id}")
        worker_script = str(spec.extra.get("worker_script") or "").strip() or _PLACEHOLDER_WORKER

        with rollback_on_error(self.label) as tracker:
            self._put_worker(token, account_id, script_name, worker_script, spec.env_vars)
            tracker.track(script_name, lambda: self._delete_worker(token, account_id, script_name))

            # Enable the workers.dev subdomain route so the worker is reachable.
            subdomain = self._workers_subdomain(token, account_id)
            endpoint = f"https://{script_name}.{subdomain}.workers.dev" if subdomain else None

            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.PAAS,
                resource_id=f"{account_id}/{script_name}",
                resource_ids=[f"{account_id}/{script_name}"],
                status=DeploymentStatus.RUNNING,
                endpoints=[endpoint] if endpoint else [],
                connection_meta={"account_id": account_id, "script_name": script_name},
                metadata={"used_placeholder": worker_script is _PLACEHOLDER_WORKER},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._require(credentials, "cloudflare_api_token")
        account_id, script_name = self._split(resource_id)
        try:
            self._get(token, f"/accounts/{account_id}/workers/scripts/{script_name}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="worker not found")
            raise
        return StatusResult(status=DeploymentStatus.RUNNING)

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token = self._require(credentials, "cloudflare_api_token")
        account_id, script_name = self._split(resource_id)
        self._delete_worker(token, account_id, script_name)

    # --- helpers ---
    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower()).strip("-")
        return (cleaned or "lp-worker")[:63]

    @staticmethod
    def _split(resource_id: str) -> tuple[str, str]:
        if "/" in resource_id:
            account_id, name = resource_id.split("/", 1)
            return account_id, name
        raise ProviderError(f"Cloudflare resource id must be 'account/script', got '{resource_id}'")

    def _first_account(self, token: str) -> str | None:
        data = self._get(token, "/accounts", params={"per_page": 1})
        results = data.get("result") or []
        return results[0]["id"] if results else None

    def _workers_subdomain(self, token: str, account_id: str) -> str | None:
        try:
            data = self._get(token, f"/accounts/{account_id}/workers/subdomain")
            return (data.get("result") or {}).get("subdomain")
        except Exception:  # noqa: BLE001 - subdomain optional
            return None

    def _put_worker(
        self,
        token: str,
        account_id: str,
        script_name: str,
        script: str,
        env_vars: Mapping[str, str],
    ) -> None:
        metadata = {
            "body_part": "script",
            "bindings": [
                {"type": "plain_text", "name": str(k), "text": str(v)} for k, v in env_vars.items()
            ],
        }
        files = {
            "metadata": (None, json.dumps(metadata), "application/json"),
            "script": ("worker.js", script, "application/javascript"),
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.put(
                f"{_API}/accounts/{account_id}/workers/scripts/{script_name}",
                headers={"Authorization": f"Bearer {token}"},
                files=files,
            )
        if resp.status_code >= 400:
            raise ProviderError(f"Cloudflare worker upload failed {resp.status_code}: {resp.text[:300]}")

    def _delete_worker(self, token: str, account_id: str, script_name: str) -> None:
        with httpx.Client(timeout=30.0) as client:
            resp = client.delete(
                f"{_API}/accounts/{account_id}/workers/scripts/{script_name}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code not in (200, 204, 404):
                logger.debug("cloudflare_worker_delete_unexpected", status=resp.status_code)

    # --- HTTP ---
    def _get(self, token: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{_API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

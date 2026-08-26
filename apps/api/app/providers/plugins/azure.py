"""Microsoft Azure VM provider (native ARM REST + cloud-init).

Boots an Azure Linux VM that runs the app container via cloud-init (delivered as the VM
``customData``). Auth is the OAuth2 client-credentials grant against Entra ID; all calls
go through ``httpx`` against Azure Resource Manager. No azure CLI, no Terraform/Ansible,
no azure-mgmt SDK.

Everything is created inside a dedicated resource group per environment, so teardown /
rollback is a single resource-group delete.

API reference: https://learn.microsoft.com/rest/api/compute/
"""

from __future__ import annotations

import base64
import secrets
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

_ARM = "https://management.azure.com"
_NET_API = "2023-05-01"
_COMPUTE_API = "2023-07-01"
_RG_API = "2021-04-01"

_POWER_MAP = {
    "PowerState/running": DeploymentStatus.RUNNING,
    "PowerState/starting": DeploymentStatus.PROVISIONING,
    "PowerState/stopping": DeploymentStatus.DEGRADED,
    "PowerState/stopped": DeploymentStatus.DEGRADED,
    "PowerState/deallocated": DeploymentStatus.DEGRADED,
}


class AzureProvider(CloudProviderAdapter):
    id = "azure"
    label = "Microsoft Azure (VM)"
    runtime_targets = (RuntimeTarget.VM, RuntimeTarget.DOCKER_HOST)
    docs_url = "https://learn.microsoft.com/rest/api/compute/"

    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(name="azure_client_id", label="Client ID", secret=True),
            CredentialField(name="azure_client_secret", label="Client Secret", secret=True),
            CredentialField(name="azure_tenant_id", label="Tenant ID", secret=True),
            CredentialField(name="azure_subscription_id", label="Subscription ID", secret=True),
            CredentialField(name="azure_location", label="Location", secret=False, required=False,
                            placeholder="eastus"),
        ]

    def regions(self, credentials: Mapping[str, str] | None = None) -> list[RegionOption]:
        return [
            RegionOption(value="eastus", label="East US"),
            RegionOption(value="eastus2", label="East US 2"),
            RegionOption(value="westus2", label="West US 2"),
            RegionOption(value="westeurope", label="West Europe"),
            RegionOption(value="northeurope", label="North Europe"),
            RegionOption(value="southeastasia", label="Southeast Asia"),
        ]

    def tiers(self, credentials: Mapping[str, str] | None = None) -> list[ComputeTier]:
        return [
            ComputeTier(id="Standard_B1s", label="B1s - 1 vCPU / 1 GB", vcpus=1, memory_mb=1024),
            ComputeTier(id="Standard_B2s", label="B2s - 2 vCPU / 4 GB", vcpus=2, memory_mb=4096),
            ComputeTier(id="Standard_B2ms", label="B2ms - 2 vCPU / 8 GB", vcpus=2, memory_mb=8192),
            ComputeTier(id="Standard_D2s_v5", label="D2s v5 - 2 vCPU / 8 GB", vcpus=2, memory_mb=8192),
        ]

    # --- lifecycle ---
    def validate_credentials(self, credentials: Mapping[str, str]) -> bool:
        try:
            token = self._token(credentials)
            sub = self._require(credentials, "azure_subscription_id")
            self._get(token, f"/subscriptions/{sub}", api="2020-01-01")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("azure_validate_failed", error=str(exc)[:200])
            return False

    def provision(
        self,
        environment_id: str,
        spec: ProvisionSpec,
        *,
        credentials: Mapping[str, str],
    ) -> ProvisionResult:
        if not spec.image:
            raise CredentialError("Azure VM provider requires spec.image (a container image)")
        token = self._token(credentials)
        sub = self._require(credentials, "azure_subscription_id")
        location = spec.region or credentials.get("azure_location") or "eastus"
        vm_size = spec.tier or "Standard_B2s"
        base = self._safe_name(spec.name or f"lp-{environment_id}")
        rg = f"launchpad-{base}"[:90]
        user_data = render_cloud_init(
            image=spec.image,
            app_port=spec.app_port,
            env_vars=spec.env_vars,
            ssh_authorized_keys=[spec.ssh_public_key] if spec.ssh_public_key else (),
        )
        custom_data = base64.b64encode(user_data.encode()).decode()

        with rollback_on_error(self.label) as tracker:
            # Dedicated resource group => teardown is a single delete.
            self._put(token, f"/subscriptions/{sub}/resourcegroups/{rg}", _RG_API,
                      {"location": location, "tags": {"launchpad-environment": environment_id}})
            tracker.track(rg, lambda: self._delete_resource_group(token, sub, rg))

            rg_path = f"/subscriptions/{sub}/resourceGroups/{rg}/providers"
            # Network security group (allow 22 + app_port).
            nsg = self._put(token, f"{rg_path}/Microsoft.Network/networkSecurityGroups/{base}-nsg", _NET_API,
                            {"location": location, "properties": {
                                "securityRules": self._security_rules(spec.app_port)}})
            # VNet + subnet.
            self._put(token, f"{rg_path}/Microsoft.Network/virtualNetworks/{base}-vnet", _NET_API,
                      {"location": location, "properties": {
                          "addressSpace": {"addressPrefixes": ["10.10.0.0/16"]},
                          "subnets": [{"name": "default", "properties": {"addressPrefix": "10.10.0.0/24"}}]}})
            subnet_id = f"{rg_path}/Microsoft.Network/virtualNetworks/{base}-vnet/subnets/default"
            # Public IP.
            pip = self._put(token, f"{rg_path}/Microsoft.Network/publicIPAddresses/{base}-pip", _NET_API,
                            {"location": location, "properties": {"publicIPAllocationMethod": "Static"},
                             "sku": {"name": "Standard"}})
            # NIC.
            nic = self._put(token, f"{rg_path}/Microsoft.Network/networkInterfaces/{base}-nic", _NET_API,
                            {"location": location, "properties": {
                                "networkSecurityGroup": {"id": nsg["id"]},
                                "ipConfigurations": [{"name": "ipcfg", "properties": {
                                    "subnet": {"id": f"{_ARM}{subnet_id}"},
                                    "publicIPAddress": {"id": pip["id"]}}}]}})
            # VM.
            admin_password = None
            os_profile: dict[str, Any] = {
                "computerName": base[:15] or "lpvm",
                "adminUsername": "azureuser",
                "customData": custom_data,
            }
            if spec.ssh_public_key:
                os_profile["linuxConfiguration"] = {
                    "disablePasswordAuthentication": True,
                    "ssh": {"publicKeys": [{
                        "path": "/home/azureuser/.ssh/authorized_keys",
                        "keyData": spec.ssh_public_key}]},
                }
            else:
                admin_password = f"Lp{secrets.token_urlsafe(16)}!aA1"
                os_profile["adminPassword"] = admin_password
            self._put(token, f"{rg_path}/Microsoft.Compute/virtualMachines/{base}-vm", _COMPUTE_API,
                      {"location": location, "properties": {
                          "hardwareProfile": {"vmSize": vm_size},
                          "storageProfile": {"imageReference": {
                              "publisher": "Canonical", "offer": "0001-com-ubuntu-server-jammy",
                              "sku": "22_04-lts-gen2", "version": "latest"}},
                          "osProfile": os_profile,
                          "networkProfile": {"networkInterfaces": [{"id": nic["id"]}]}}})

            ipv4 = self._pip_address(token, sub, rg, f"{base}-pip")
            return ProvisionResult(
                provider=self.id,
                runtime_target=RuntimeTarget.VM,
                resource_id=rg,
                resource_ids=[rg],
                status=DeploymentStatus.PROVISIONING,
                ip_address=ipv4,
                endpoints=[f"http://{ipv4}:{spec.app_port}"] if ipv4 else [],
                connection_meta={"ssh_user": "azureuser", "ssh_port": 22, "app_port": spec.app_port,
                                 "resource_group": rg, "vm_name": f"{base}-vm", "location": location},
                tags={"launchpad-environment": environment_id},
                metadata={"vm_size": vm_size, "location": location,
                          "admin_password_generated": admin_password is not None},
            )

    def get_status(self, resource_id: str, *, credentials: Mapping[str, str]) -> StatusResult:
        token = self._token(credentials)
        sub = self._require(credentials, "azure_subscription_id")
        rg = resource_id
        base = rg.removeprefix("launchpad-")
        try:
            iv = self._get(
                token,
                f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Compute"
                f"/virtualMachines/{base}-vm/instanceView",
                api=_COMPUTE_API,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return StatusResult(status=DeploymentStatus.DESTROYED, message="vm not found")
            raise
        power = next((s.get("code") for s in iv.get("statuses", [])
                      if str(s.get("code", "")).startswith("PowerState/")), None)
        ipv4 = self._pip_address(token, sub, rg, f"{base}-pip")
        return StatusResult(
            status=_POWER_MAP.get(power or "", DeploymentStatus.PROVISIONING),
            ip_address=ipv4,
            endpoints=[f"http://{ipv4}"] if ipv4 else [],
            raw={"power_state": power},
        )

    def destroy(self, resource_id: str, *, credentials: Mapping[str, str]) -> None:
        token = self._token(credentials)
        sub = self._require(credentials, "azure_subscription_id")
        self._delete_resource_group(token, sub, resource_id)

    # --- helpers ---
    def _token(self, credentials: Mapping[str, str]) -> str:
        client_id = self._require(credentials, "azure_client_id")
        client_secret = self._require(credentials, "azure_client_secret")
        tenant = self._require(credentials, "azure_tenant_id")
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": f"{_ARM}/.default",
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data=data,
            )
        if resp.status_code >= 400:
            raise CredentialError(f"Azure token request failed {resp.status_code}: {resp.text[:200]}")
        return resp.json()["access_token"]

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower()).strip("-")
        return (cleaned or "lpvm")[:40]

    @staticmethod
    def _security_rules(app_port: int) -> list[dict[str, Any]]:
        rules = []
        for i, port in enumerate(sorted({22, int(app_port)})):
            rules.append({
                "name": f"allow-{port}",
                "properties": {
                    "priority": 1000 + i,
                    "protocol": "Tcp",
                    "access": "Allow",
                    "direction": "Inbound",
                    "sourceAddressPrefix": "*",
                    "sourcePortRange": "*",
                    "destinationAddressPrefix": "*",
                    "destinationPortRange": str(port),
                },
            })
        return rules

    def _pip_address(self, token: str, sub: str, rg: str, pip_name: str) -> str | None:
        try:
            pip = self._get(
                token,
                f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Network"
                f"/publicIPAddresses/{pip_name}",
                api=_NET_API,
            )
            return (pip.get("properties", {}) or {}).get("ipAddress")
        except Exception:  # noqa: BLE001 - not allocated yet
            return None

    def _delete_resource_group(self, token: str, sub: str, rg: str) -> None:
        with httpx.Client(timeout=60.0) as client:
            resp = client.delete(
                f"{_ARM}/subscriptions/{sub}/resourcegroups/{rg}",
                headers=self._headers(token),
                params={"api-version": _RG_API},
            )
            if resp.status_code not in (200, 202, 204, 404):
                logger.warning("azure_rg_delete_unexpected", rg=rg, status=resp.status_code)

    # --- HTTP ---
    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, token: str, path: str, *, api: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{_ARM}{path}", headers=self._headers(token), params={"api-version": api})
            resp.raise_for_status()
            return resp.json()

    def _put(self, token: str, path: str, api: str, body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=120.0) as client:
            resp = client.put(f"{_ARM}{path}", headers=self._headers(token),
                              params={"api-version": api}, json=body)
        if resp.status_code >= 400:
            raise ProviderError(f"Azure PUT {path} failed {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

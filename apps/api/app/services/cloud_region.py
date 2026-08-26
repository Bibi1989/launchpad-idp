"""Resolve cloud region/location from wizard snapshots, plugins, and vault preferences."""

from __future__ import annotations

import re

from app.schemas.cloud import CloudCredentials, CloudProvider
from app.services.cloud_networks import _normalize_aws_region
from app.services.cloud_promote import default_region

_GCP_DEFAULT = "europe-west3"
_AWS_DEFAULT = "eu-central-1"
_AZURE_DEFAULT = "westeurope"

_AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_GCP_REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_TFVARS_REGION_RE = re.compile(r'^\s*region\s*=\s*"([^"]+)"', re.MULTILINE)
_LAUNCHPAD_REGION_RE = re.compile(r"""REGION='([^']+)'""")


def preferred_region_from_credentials(
    provider: str,
    credentials: CloudCredentials | None,
) -> str | None:
    if credentials is None:
        return None
    raw = (provider or "").strip().lower()
    if raw == CloudProvider.GCP.value:
        return (credentials.gcp_region or "").strip() or None
    if raw == CloudProvider.AWS.value:
        region = (credentials.aws_region or "").strip() or None
        if region and _AWS_REGION_RE.match(region):
            return region
        return None
    if raw == CloudProvider.AZURE.value:
        return (credentials.azure_location or "").strip() or None

    # Best-effort when caller passes a non-provider token (e.g. "local").
    # Prefer GCP region when present, else fall back to other supported
    # credential fields.
    gcp_region = (credentials.gcp_region or "").strip() or None
    if gcp_region:
        return gcp_region
    aws_region = (credentials.aws_region or "").strip() or None
    if aws_region and _AWS_REGION_RE.match(aws_region):
        return aws_region
    azure_location = (credentials.azure_location or "").strip() or None
    if azure_location:
        return azure_location
    return None


def _region_from_resources(provider: str, resources: dict[str, object]) -> str | None:
    raw = (provider or "").strip().lower()
    if raw == CloudProvider.AZURE.value:
        value = str(resources.get("location") or resources.get("region") or "").strip()
    else:
        value = str(resources.get("region") or resources.get("location") or "").strip()
    return value or None


def _coerce_region(provider: str, region: str | None) -> str | None:
    cleaned = (region or "").strip()
    if not cleaned:
        return None
    raw = (provider or "").strip().lower()
    if raw == CloudProvider.AWS.value:
        normalized = _normalize_aws_region(cleaned, fallback="")
        return normalized or None
    if raw == CloudProvider.GCP.value and _GCP_REGION_RE.match(cleaned):
        return cleaned
    if raw == CloudProvider.AZURE.value:
        return cleaned
    return cleaned


def region_from_wizard(
    provider: str,
    snapshot: dict | None,
    credentials: CloudCredentials | None = None,
) -> str:
    """Pick region for cloud deploy/teardown (GKE/EKS/VM/IaC).

    Priority:
    1. Region/location from Settings credentials (vault preferred)
    2. ``cloud_plugin.region`` when set (explicit deploy-target choice)
    3. ``cloud.resources`` region/location when not the provider generic default
    4. ``running_instance.region`` when provider-appropriate
    5. Provider default (fallback)
    """
    try:
        fallback = default_region(CloudProvider(provider))
    except ValueError:
        fallback = _GCP_DEFAULT

    plugin_region: str | None = None
    resources_region: str | None = None
    running_region: str | None = None

    if isinstance(snapshot, dict):
        plugin = snapshot.get("cloud_plugin")
        if isinstance(plugin, dict):
            plugin_region = _coerce_region(provider, str(plugin.get("region") or "").strip() or None)

        cloud = snapshot.get("cloud")
        if isinstance(cloud, dict):
            resources = cloud.get("resources")
            if isinstance(resources, dict):
                resources_region = _coerce_region(provider, _region_from_resources(provider, resources))

        running = snapshot.get("running_instance")
        if isinstance(running, dict):
            running_region = _coerce_region(provider, str(running.get("region") or "").strip() or None)

    cred_region = _coerce_region(provider, preferred_region_from_credentials(provider, credentials))
    if cred_region:
        return cred_region

    if plugin_region:
        return plugin_region
    if resources_region and resources_region != fallback:
        return resources_region
    if running_region and running_region != fallback:
        return running_region

    if resources_region:
        return resources_region

    if running_region:
        return running_region

    if cred_region:
        return cred_region

    return fallback


def region_from_bundle_files(
    files: dict[str, str],
    provider: CloudProvider,
) -> str | None:
    """Best-effort region from generated workspace files (for CI workflow auth steps)."""
    candidates: list[str] = []
    for path in (
        "infra/terraform/terraform.tfvars",
        "terraform.tfvars",
        "infra/terraform.tfvars",
        "infra/launchProvision.sh",
        "provision/launchpad.sh",
    ):
        body = files.get(path) or ""
        if not body:
            continue
        if path.endswith(".tfvars") or "tfvars" in path:
            match = _TFVARS_REGION_RE.search(body)
            if match:
                candidates.append(match.group(1))
        if path.endswith("launchProvision.sh") or path.endswith("launchpad.sh"):
            match = _LAUNCHPAD_REGION_RE.search(body)
            if match:
                candidates.append(match.group(1))

    for raw in candidates:
        coerced = _coerce_region(provider.value, raw)
        if coerced:
            return coerced
    return None

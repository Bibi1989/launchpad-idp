"""List cloud VPC/networks using account vault credentials."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger, sanitize_log_message
from app.core.secrets import has_aws_auth, has_gcp_auth
from app.schemas.cloud import CloudCredentials, CloudProvider
from app.schemas.user_credentials import (
    CloudNetworkListResponse,
    CloudNetworkOption,
    CloudSecurityGroupListResponse,
    CloudSecurityGroupOption,
)

logger = get_logger(__name__)


class CloudNetworkListError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def list_cloud_networks(
    *,
    provider: str,
    credentials: CloudCredentials,
    region: str | None = None,
) -> CloudNetworkListResponse:
    """Return VPC/networks visible with the given credentials."""
    normalized = (provider or "").strip().lower()
    if normalized == CloudProvider.AWS.value:
        return _list_aws(credentials=credentials, region=region)
    if normalized == CloudProvider.GCP.value:
        return _list_gcp(credentials=credentials, region=region)
    if normalized == CloudProvider.AZURE.value:
        return CloudNetworkListResponse(provider=CloudProvider.AZURE.value, networks=[])
    raise CloudNetworkListError(
        "unsupported_provider",
        f"Network listing is not supported for provider '{provider}'",
    )


def _credential_env(credentials: CloudCredentials, *, provider: str) -> dict[str, str]:
    from app.services.cloud_instance_compute import _credential_env as build_env

    return build_env(
        credentials,
        environment_id=f"netlist-{uuid4().hex[:12]}",
        provider=provider,
    )


def _normalize_aws_region(region: str | None, *, fallback: str = "us-east-1") -> str:
    """Reject GCP-style / empty regions so EC2 is never called as ec2.us-central1…"""
    import re

    raw = (region or "").strip().lower()
    if not raw:
        return fallback
    # AWS regions look like us-east-1, eu-central-1, ap-northeast-2.
    if re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", raw):
        return raw
    return fallback


def _list_aws(*, credentials: CloudCredentials, region: str | None) -> CloudNetworkListResponse:
    if not has_aws_auth(credentials):
        raise CloudNetworkListError(
            "credentials_required",
            "AWS credentials are required to list VPCs. Save keys in Settings or Connect SSO.",
        )
    from app.services.aws_client import AwsClientError, list_vpcs

    env = _credential_env(credentials, provider=CloudProvider.AWS.value)
    resolved = _normalize_aws_region(region or credentials.aws_region)
    try:
        rows = list_vpcs(env=env, region=resolved)
    except AwsClientError as exc:
        raise CloudNetworkListError("aws_list_failed", str(exc)) from exc
    networks = [
        CloudNetworkOption(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or row.get("id") or ""),
            cidr=row.get("cidr"),
            is_default=bool(row.get("is_default")),
            region=resolved,
        )
        for row in rows
        if str(row.get("id") or "").strip()
    ]
    return CloudNetworkListResponse(
        provider=CloudProvider.AWS.value,
        region=resolved,
        networks=networks,
    )


def _list_gcp(*, credentials: CloudCredentials, region: str | None) -> CloudNetworkListResponse:
    if not has_gcp_auth(credentials):
        raise CloudNetworkListError(
            "credentials_required",
            "GCP credentials are required to list networks. Save a SA key or Connect in Settings.",
        )
    if shutil.which("gcloud") is None:
        raise CloudNetworkListError(
            "gcloud_missing",
            "gcloud CLI is required on the API/worker host to list GCP networks.",
        )
    env = _credential_env(credentials, provider=CloudProvider.GCP.value)
    resolved = (region or credentials.gcp_region or "us-central1").strip() or "us-central1"
    cmd = [
        "gcloud",
        "compute",
        "networks",
        "list",
        "--format=json",
        "--quiet",
    ]
    project = (
        (credentials.gcp_project_id or "").strip()
        or env.get("CLOUDSDK_CORE_PROJECT")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()
    if project:
        cmd.append(f"--project={project}")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env={**os.environ, **env},
        )
    except Exception as exc:  # noqa: BLE001
        raise CloudNetworkListError(
            "gcp_list_failed",
            sanitize_log_message(str(exc)[:300]),
        ) from exc
    if completed.returncode != 0:
        detail = sanitize_log_message((completed.stderr or completed.stdout or "list failed")[:400])
        raise CloudNetworkListError("gcp_list_failed", detail)
    try:
        payload: Any = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise CloudNetworkListError("gcp_list_failed", "Invalid gcloud JSON response") from exc
    networks: list[CloudNetworkOption] = []
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            cidr = None
            if isinstance(row.get("IPv4Range"), str):
                cidr = row["IPv4Range"]
            elif row.get("autoCreateSubnetworks") is True:
                cidr = "auto"
            networks.append(
                CloudNetworkOption(
                    id=name,
                    name=name,
                    cidr=cidr,
                    is_default=name == "default",
                    region=resolved,
                )
            )
    return CloudNetworkListResponse(
        provider=CloudProvider.GCP.value,
        region=resolved,
        networks=networks,
    )


def list_cloud_security_groups(
    *,
    provider: str,
    credentials: CloudCredentials,
    region: str | None = None,
    vpc_id: str | None = None,
) -> CloudSecurityGroupListResponse:
    """Return AWS security groups visible with the given credentials."""
    normalized = (provider or "").strip().lower()
    if normalized != CloudProvider.AWS.value:
        return CloudSecurityGroupListResponse(provider=normalized, security_groups=[])
    if not has_aws_auth(credentials):
        raise CloudNetworkListError(
            "credentials_required",
            "AWS credentials are required to list security groups. Save keys in Settings or Connect SSO.",
        )
    from app.services.aws_client import AwsClientError, list_security_groups

    env = _credential_env(credentials, provider=CloudProvider.AWS.value)
    resolved = _normalize_aws_region(region or credentials.aws_region)
    vpc = (vpc_id or "").strip() or None
    try:
        rows = list_security_groups(env=env, region=resolved, vpc_id=vpc)
    except AwsClientError as exc:
        raise CloudNetworkListError("aws_list_failed", str(exc)) from exc
    groups = [
        CloudSecurityGroupOption(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or row.get("id") or ""),
            vpc_id=row.get("vpc_id"),
            description=row.get("description"),
            region=resolved,
        )
        for row in rows
        if str(row.get("id") or "").strip()
    ]
    return CloudSecurityGroupListResponse(
        provider=CloudProvider.AWS.value,
        region=resolved,
        vpc_id=vpc,
        security_groups=groups,
    )

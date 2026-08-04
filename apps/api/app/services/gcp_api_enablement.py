"""Enable required Google Cloud APIs before Terraform provision.

Uses the workspace service-account JSON against Service Usage so APIs such as
``container.googleapis.com`` are active *before* plan/apply (not only via
``google_project_service`` during the same apply, which races with GKE create).

Terraform's ``google_project_service`` also requires
``cloudresourcemanager.googleapis.com`` already enabled - otherwise apply fails
with ``accessNotConfigured`` even when ``apis.tf`` lists the right services.
This module bootstraps Resource Manager + Service Usage first, then the rest.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.core.logging import get_logger
from app.core.secrets import project_id_from_gcp_sa_json

logger = get_logger(__name__)

_CLOUD_PLATFORM_SCOPE = ("https://www.googleapis.com/auth/cloud-platform",)
_SERVICE_USAGE = "https://serviceusage.googleapis.com/v1"

# Must be ENABLED before Terraform can manage google_project_service resources.
_BOOTSTRAP_APIS: tuple[str, ...] = (
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
)


class GcpApiEnablementError(RuntimeError):
    """Failed to enable one or more Google APIs."""


@dataclass
class GcpApiEnablementResult:
    project_id: str
    required: list[str] = field(default_factory=list)
    already_enabled: list[str] = field(default_factory=list)
    newly_enabled: list[str] = field(default_factory=list)
    waited_seconds: float = 0.0


def _authorized_session(sa_json: str) -> AuthorizedSession:
    try:
        info = json.loads(sa_json)
    except json.JSONDecodeError as exc:
        raise GcpApiEnablementError("GCP service account JSON is invalid") from exc
    if not isinstance(info, dict):
        raise GcpApiEnablementError("GCP service account JSON must be an object")
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=_CLOUD_PLATFORM_SCOPE,
    )
    return AuthorizedSession(credentials)


def _service_name(project_id: str, api: str) -> str:
    return f"projects/{project_id}/services/{api}"


def _is_enabled(state: str | None) -> bool:
    return (state or "").upper() == "ENABLED"


def _get_service_state(session: AuthorizedSession, project_id: str, api: str) -> str:
    url = f"{_SERVICE_USAGE}/{_service_name(project_id, api)}"
    response = session.get(url, timeout=60)
    if response.status_code == 404:
        return "DISABLED"
    if response.status_code >= 400:
        raise GcpApiEnablementError(
            f"Failed to read API state for {api}: "
            f"HTTP {response.status_code} {response.text[:300]}"
        )
    payload = response.json()
    return str(payload.get("state") or "DISABLED")


def _wait_operation(
    session: AuthorizedSession,
    operation_name: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"{_SERVICE_USAGE}/{operation_name}"
    while time.monotonic() < deadline:
        response = session.get(url, timeout=60)
        if response.status_code >= 400:
            raise GcpApiEnablementError(
                f"Failed polling Service Usage operation: "
                f"HTTP {response.status_code} {response.text[:300]}"
            )
        payload: dict[str, Any] = response.json()
        if payload.get("done"):
            if payload.get("error"):
                err = payload["error"]
                message = err.get("message") if isinstance(err, dict) else str(err)
                raise GcpApiEnablementError(f"Enable APIs operation failed: {message}")
            return
        time.sleep(2.0)
    raise GcpApiEnablementError(
        f"Timed out waiting for Service Usage operation {operation_name}"
    )


def _batch_enable(
    session: AuthorizedSession,
    project_id: str,
    service_ids: list[str],
    *,
    timeout_seconds: float,
) -> None:
    if not service_ids:
        return
    response = session.post(
        f"{_SERVICE_USAGE}/projects/{project_id}/services:batchEnable",
        json={"serviceIds": service_ids},
        timeout=120,
    )
    if response.status_code >= 400:
        hint = ""
        body = response.text[:500]
        if "cloudresourcemanager" in body.lower() or "accessNotConfigured" in body:
            hint = (
                " Open the Google Cloud console and enable "
                "Cloud Resource Manager API + Service Usage API once, then retry: "
                f"https://console.developers.google.com/apis/api/"
                f"cloudresourcemanager.googleapis.com/overview?project={project_id}"
            )
        raise GcpApiEnablementError(
            f"Failed to enable APIs {service_ids}: "
            f"HTTP {response.status_code} {body}.{hint}"
        )
    operation = response.json()
    op_name = operation.get("name")
    if not isinstance(op_name, str) or not op_name:
        raise GcpApiEnablementError("Service Usage batchEnable returned no operation name")
    _wait_operation(session, op_name, timeout_seconds=timeout_seconds)


def _confirm_enabled(
    session: AuthorizedSession,
    project_id: str,
    service_ids: list[str],
    *,
    timeout_seconds: float,
) -> list[str]:
    """Wait until every service reports ENABLED. Returns still-pending IDs."""
    confirm_deadline = time.monotonic() + min(60.0, timeout_seconds)
    still_pending = list(service_ids)
    while still_pending and time.monotonic() < confirm_deadline:
        remaining: list[str] = []
        for api in still_pending:
            if not _is_enabled(_get_service_state(session, project_id, api)):
                remaining.append(api)
        still_pending = remaining
        if still_pending:
            time.sleep(2.0)
    return still_pending


def _ordered_enable_batches(apis: list[str]) -> list[list[str]]:
    """Bootstrap Resource Manager + Service Usage before other APIs."""
    bootstrap = [api for api in _BOOTSTRAP_APIS if api in apis]
    rest = [api for api in apis if api not in set(_BOOTSTRAP_APIS)]
    batches: list[list[str]] = []
    if bootstrap:
        batches.append(bootstrap)
    if rest:
        batches.append(rest)
    return batches


def enable_gcp_apis(
    *,
    sa_json: str,
    project_id: str | None,
    apis: list[str],
    timeout_seconds: float = 240.0,
) -> GcpApiEnablementResult:
    """Enable ``apis`` on the project using the service account. Idempotent."""
    resolved_project = (project_id or "").strip() or (project_id_from_gcp_sa_json(sa_json) or "")
    if not resolved_project:
        raise GcpApiEnablementError(
            "GCP project_id is required (from the service account JSON or workspace config)"
        )
    # Always ensure bootstrap APIs are present - Terraform cannot manage
    # google_project_service without Cloud Resource Manager.
    merged: list[str] = []
    seen: set[str] = set()
    for api in [*_BOOTSTRAP_APIS, *[a.strip() for a in apis if a and a.strip()]]:
        if api not in seen:
            seen.add(api)
            merged.append(api)
    required = merged
    if not required:
        return GcpApiEnablementResult(project_id=resolved_project)

    session = _authorized_session(sa_json)
    already: list[str] = []
    pending: list[str] = []
    for api in required:
        state = _get_service_state(session, resolved_project, api)
        if _is_enabled(state):
            already.append(api)
        else:
            pending.append(api)

    result = GcpApiEnablementResult(
        project_id=resolved_project,
        required=list(required),
        already_enabled=already,
    )
    if not pending:
        logger.info(
            "gcp_apis_already_enabled",
            project_id=resolved_project,
            apis=already,
        )
        return result

    logger.info(
        "gcp_apis_enable_start",
        project_id=resolved_project,
        apis=pending,
    )
    started = time.monotonic()

    for batch in _ordered_enable_batches(pending):
        logger.info(
            "gcp_apis_enable_batch",
            project_id=resolved_project,
            apis=batch,
        )
        _batch_enable(
            session,
            resolved_project,
            batch,
            timeout_seconds=timeout_seconds,
        )
        still = _confirm_enabled(
            session,
            resolved_project,
            batch,
            timeout_seconds=timeout_seconds,
        )
        if still:
            raise GcpApiEnablementError(
                "APIs were submitted for enablement but are not ENABLED yet: "
                + ", ".join(still)
                + ". Wait a minute and retry provision. If cloudresourcemanager.googleapis.com "
                "is listed, enable it once in the Google Cloud console first: "
                f"https://console.developers.google.com/apis/api/"
                f"cloudresourcemanager.googleapis.com/overview?project={resolved_project}"
            )
        result.newly_enabled.extend(batch)

    result.waited_seconds = round(time.monotonic() - started, 1)
    logger.info(
        "gcp_apis_enable_ok",
        project_id=resolved_project,
        newly_enabled=result.newly_enabled,
        waited_seconds=result.waited_seconds,
    )
    return result

"""Capture workspace credentials + wizard metadata for async cloud teardown.

Workspace destroy deletes the provisioning workspace (and its encrypted
credentials) before Celery teardown often runs. Persisting a sealed snapshot on
the environment row lets attach/cloud cleanup still authenticate and delete VMs,
VPCs, and serverless services.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.secrets import encrypt_secret
from app.models.domain import Environment, ProvisioningWorkspace
from app.schemas.cloud import WorkspaceWizardConfig

logger = get_logger(__name__)


def _wizard_network_flags(snapshot: dict[str, Any] | None) -> tuple[bool, bool]:
    if not isinstance(snapshot, dict):
        return False, False
    cloud = snapshot.get("cloud")
    if not isinstance(cloud, dict):
        return False, False
    resources = cloud.get("resources")
    if not isinstance(resources, dict):
        return False, False
    create_vpc = bool(resources.get("vpc") or resources.get("vnet"))
    create_subnets = bool(resources.get("subnets"))
    if create_subnets:
        create_vpc = True
    return create_vpc, create_subnets


def _wizard_cloud_provider(snapshot: dict[str, Any] | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    cloud = snapshot.get("cloud")
    if isinstance(cloud, dict):
        raw = cloud.get("provider")
        if raw is not None:
            return str(getattr(raw, "value", raw))
    return None


async def capture_environment_teardown_context(
    session: AsyncSession,
    environment: Environment,
) -> None:
    """Seal workspace creds + running_instance onto the environment for teardown."""
    workspace_id = environment.workspace_id
    if workspace_id is None:
        return

    from app.services.provisioning import ProvisioningService

    provisioning = ProvisioningService(session)
    workspace = await session.get(ProvisioningWorkspace, workspace_id)
    if workspace is None:
        return

    snapshot = provisioning._load_wizard_snapshot(workspace)
    create_vpc, create_subnets = _wizard_network_flags(snapshot)
    wizard_provider = _wizard_cloud_provider(snapshot)

    running_instance: dict[str, Any] | None = None
    runtime_mode: str | None = None
    if snapshot is not None:
        try:
            wizard = WorkspaceWizardConfig.model_validate(
                {**snapshot, "has_credentials": False}
            )
            if wizard.running_instance is not None:
                running_instance = wizard.running_instance.model_dump(mode="json")
            if wizard.runtime_mode is not None:
                runtime_mode = str(
                    getattr(wizard.runtime_mode, "value", wizard.runtime_mode)
                )
        except Exception:
            logger.warning(
                "teardown_context_wizard_invalid",
                environment_id=str(environment.id),
                workspace_id=str(workspace_id),
            )

    payload: dict[str, Any] = {
        "workspace_id": str(workspace_id),
        "workspace_provider": workspace.provider,
        "wizard_cloud_provider": wizard_provider,
        "encrypted_credentials": workspace.encrypted_credentials,
        "running_instance": running_instance,
        "runtime_mode": runtime_mode,
        "create_vpc": create_vpc,
        "create_subnets": create_subnets,
        "owner_id": str(environment.owner_id),
        "environment_provider": environment.provider,
    }
    # Encrypt the whole blob so workspace SA JSON is not stored plaintext.
    environment.teardown_context_json = encrypt_secret(json.dumps(payload))
    logger.info(
        "teardown_context_captured",
        environment_id=str(environment.id),
        workspace_id=str(workspace_id),
        has_credentials=bool(workspace.encrypted_credentials),
        has_running_instance=running_instance is not None,
        create_vpc=create_vpc,
    )


def parse_teardown_context(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    from app.core.secrets import decrypt_secret

    try:
        plaintext = decrypt_secret(raw)
        data = json.loads(plaintext)
    except Exception:
        logger.warning("teardown_context_decrypt_failed")
        return None
    return data if isinstance(data, dict) else None


def owner_id_from_context(ctx: dict[str, Any] | None) -> UUID | None:
    if not ctx:
        return None
    raw = ctx.get("owner_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None

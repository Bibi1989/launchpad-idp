"""Workspace starring / catalog bookmark tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.domain import ProvisioningWorkspace
from app.services.provisioning import ProvisioningService


@pytest.mark.asyncio
async def test_set_workspace_starred_toggles_starred_at() -> None:
    workspace_id = uuid4()
    owner = SimpleNamespace(id=uuid4())
    row = ProvisioningWorkspace(
        id=workspace_id,
        owner_id=owner.id,
        org_id=None,
        name="demo",
        engine="terraform",
        provider="local",
        root_dir="/tmp/demo",
        status="ready",
        starred_at=None,
        created_at=datetime.now(UTC),
    )

    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    service = ProvisioningService(session)
    service.get_workspace_for_owner = AsyncMock(return_value=row)
    service.get_workspace_artifact_mode = MagicMock(return_value="iac_only")

    starred = await service.set_workspace_starred(workspace_id, owner, starred=True)
    assert starred.starred is True
    assert row.starred_at is not None

    unstarred = await service.set_workspace_starred(workspace_id, owner, starred=False)
    assert unstarred.starred is False
    assert row.starred_at is None

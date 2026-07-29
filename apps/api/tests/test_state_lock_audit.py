"""Distributed state lock and immutable audit log coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.domain import AuditAction, AuditStatus, ExecutionStage
from app.services.audit import AuditService
from app.services.state_lock import (
    PROVISIONING_IN_PROGRESS_MESSAGE,
    StateLockConflict,
    acquire_state_lock,
)


@pytest.mark.asyncio
async def test_acquire_state_lock_raises_on_conflict() -> None:
    env_id = uuid4()
    fake_lock = MagicMock()
    fake_lock.acquire = AsyncMock(return_value=False)
    fake_lock.release = AsyncMock()

    fake_client = MagicMock()
    fake_client.lock.return_value = fake_lock
    fake_client.aclose = AsyncMock()

    with patch("app.services.state_lock.redis.from_url", return_value=fake_client):
        with pytest.raises(StateLockConflict) as exc_info:
            async with acquire_state_lock(env_id, scope="environment"):
                pass

    assert PROVISIONING_IN_PROGRESS_MESSAGE in str(exc_info.value)
    fake_lock.release.assert_not_called()
    fake_client.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_acquire_state_lock_releases_in_finally() -> None:
    env_id = uuid4()
    fake_lock = MagicMock()
    fake_lock.acquire = AsyncMock(return_value=True)
    fake_lock.release = AsyncMock()

    fake_client = MagicMock()
    fake_client.lock.return_value = fake_lock
    fake_client.aclose = AsyncMock()

    with patch("app.services.state_lock.redis.from_url", return_value=fake_client):
        async with acquire_state_lock(env_id, scope="environment"):
            pass

    fake_lock.release.assert_awaited()
    fake_client.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_acquire_state_lock_releases_after_inner_exception() -> None:
    env_id = uuid4()
    fake_lock = MagicMock()
    fake_lock.acquire = AsyncMock(return_value=True)
    fake_lock.release = AsyncMock()

    fake_client = MagicMock()
    fake_client.lock.return_value = fake_lock
    fake_client.aclose = AsyncMock()

    with patch("app.services.state_lock.redis.from_url", return_value=fake_client):
        with pytest.raises(RuntimeError, match="boom"):
            async with acquire_state_lock(env_id, scope="environment"):
                raise RuntimeError("boom")

    fake_lock.release.assert_awaited()


@pytest.mark.asyncio
async def test_audit_service_records_append_only_entry() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    service = AuditService(session)
    entry = await service.record(
        action=AuditAction.PROVISION_INITIATED,
        actor_id=str(uuid4()),
        status=AuditStatus.PENDING,
        environment_id=uuid4(),
        workspace_id=uuid4(),
        commit_sha="abc1234",
        detail="queued",
    )

    session.add.assert_called_once()
    session.flush.assert_awaited()
    session.refresh.assert_awaited()
    assert entry.action == AuditAction.PROVISION_INITIATED
    assert entry.status == AuditStatus.PENDING


def test_execution_stage_values() -> None:
    assert ExecutionStage.INIT.value == "INIT"
    assert ExecutionStage.VALIDATE.value == "VALIDATE"
    assert ExecutionStage.PLAN.value == "PLAN"
    assert ExecutionStage.BUILD.value == "BUILD"
    assert ExecutionStage.APPLY.value == "APPLY"


def test_sandbox_bootstrap_emits_structured_stages() -> None:
    import tempfile
    from pathlib import Path

    from app.services.sandbox_runner import build_provision_bootstrap

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tf = root / "infra" / "terraform"
        tf.mkdir(parents=True)
        (tf / "main.tf").write_text("resource \"null_resource\" \"x\" {}\n", encoding="utf-8")
        cmd = build_provision_bootstrap(root, engine="terraform")
        assert cmd is not None
        assert '"stage":"INIT"' in cmd
        assert '"stage":"VALIDATE"' in cmd
        assert '"stage":"PLAN"' in cmd
        assert '"stage":"APPLY"' in cmd
        assert "terraform validate" in cmd
        assert "terraform plan -out=tfplan" in cmd
        assert "terraform apply -auto-approve -input=false tfplan" in cmd

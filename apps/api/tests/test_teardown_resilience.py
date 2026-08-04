"""Teardown must always remove an environment, even if cluster cleanup fails.

Regression for environments stuck in TEARDOWN_PENDING/FAILED when the worker
cannot reach the cluster (clients not loaded), which left them undeletable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_teardown_marks_destroyed_when_cluster_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.domain import EnvironmentStatus
    from app.workers import tasks as task_module

    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.name = "demo"
    environment.namespace_name = "launchpad-env-demo"
    environment.status = EnvironmentStatus.TEARDOWN_PENDING
    environment.latest_commit_sha = None
    environment.owner_id = uuid4()
    environment.workspace_id = None
    environment.provider = "local"
    environment.workload_image = None

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session_factory = MagicMock(return_value=session)

    env_repo = MagicMock()
    env_repo.get_by_id = AsyncMock(return_value=environment)
    env_repo.update_status = AsyncMock(return_value=environment)
    log_repo = MagicMock()
    log_repo.create = AsyncMock()

    monkeypatch.setattr(task_module, "_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_module, "EnvironmentRepository", lambda _s: env_repo)
    monkeypatch.setattr(task_module, "DeploymentLogRepository", lambda _s: log_repo)
    monkeypatch.setattr(task_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr("app.services.preview_tunnel.stop_preview_tunnel", lambda *_: None)

    record_audit = AsyncMock()
    monkeypatch.setattr(task_module, "_record_audit", record_audit)
    fail_execution = AsyncMock()
    monkeypatch.setattr(task_module, "_fail_execution", fail_execution)

    provisioner = MagicMock()
    # Cluster unreachable at teardown time (e.g. kubeconfig/context down).
    provisioner.teardown.side_effect = RuntimeError("cluster unreachable")
    monkeypatch.setattr(task_module, "KubernetesProvisioner", lambda _s: provisioner)

    lock_cm = AsyncMock()
    lock_cm.__aenter__ = AsyncMock(return_value=None)
    lock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.workers.tasks.publish_env_event", new_callable=AsyncMock),
        patch("app.workers.tasks.acquire_state_lock", return_value=lock_cm),
    ):
        await task_module._run_teardown(str(env_id), "corr-teardown")

    # Cleanup raised, but the environment is still marked DESTROYED (not FAILED).
    assert env_repo.update_status.await_args_list, "expected a status update"
    final = env_repo.update_status.await_args_list[-1]
    assert final.args[1] == EnvironmentStatus.DESTROYED
    fail_execution.assert_not_awaited()

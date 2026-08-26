from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.git_urls import (
    branch_from_git_ref,
    normalize_git_repo_full_name,
    short_commit_sha,
)
from app.services.webhook import GitHubWebhookService


def test_normalize_git_repo_variants() -> None:
    assert normalize_git_repo_full_name("https://github.com/Acme/Demo.git") == "acme/demo"
    assert normalize_git_repo_full_name("git@github.com:Acme/Demo.git") == "acme/demo"
    assert normalize_git_repo_full_name("Acme/Demo") == "acme/demo"


def test_branch_and_short_sha() -> None:
    assert branch_from_git_ref("refs/heads/feature/x") == "feature/x"
    assert branch_from_git_ref("refs/tags/v1") is None
    assert short_commit_sha("a8f9c12deadbeef") == "a8f9c12"
    assert short_commit_sha("0000000000000000000000000000000000000000") == ""


def test_verify_signature_accepts_valid_hmac() -> None:
    secret = "test-webhook-secret"
    body = b'{"ref":"refs/heads/main"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert GitHubWebhookService.verify_signature(
        body=body,
        signature_header=f"sha256={digest}",
        secret=secret,
    )


def test_verify_signature_rejects_invalid() -> None:
    assert not GitHubWebhookService.verify_signature(
        body=b"{}",
        signature_header="sha256=deadbeef",
        secret="secret",
    )
    assert not GitHubWebhookService.verify_signature(
        body=b"{}",
        signature_header=None,
        secret="secret",
    )


def test_parse_push_event() -> None:
    payload = {
        "ref": "refs/heads/main",
        "after": "a8f9c12abcdef0123456789",
        "repository": {"full_name": "acme/demo"},
    }
    details = GitHubWebhookService.parse_push_event(payload)
    assert details is not None
    assert details.branch == "main"
    assert details.commit_sha == "a8f9c12"
    assert details.repository_full_name == "acme/demo"


@pytest.mark.asyncio
async def test_process_push_enqueues_rebuild() -> None:
    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    service = GitHubWebhookService(session)
    service._environments = MagicMock()
    service._environments.list_active_for_any_linked_repo = AsyncMock(
        return_value=[environment]
    )
    service._environments.mark_rebuild = AsyncMock(return_value=environment)
    service._logs = MagicMock()
    service._logs.create = AsyncMock()

    payload = {
        "ref": "refs/heads/main",
        "after": "a8f9c12abcdef0123456789",
        "repository": {"full_name": "acme/demo"},
    }

    with (
        patch("app.services.webhook.publish_env_event", new_callable=AsyncMock) as publish,
        patch("app.services.webhook.is_state_locked", new_callable=AsyncMock, return_value=False),
        patch("app.workers.tasks.enqueue_rebuild_environment") as enqueue,
    ):
        result = await service.process_event(
            event_name="push",
            payload=payload,
            correlation_id="test-corr",
        )

    assert result.matched_environment_ids == [env_id]
    enqueue.assert_called_once_with(
        environment_id=str(env_id),
        commit_sha="a8f9c12",
        correlation_id="test-corr",
    )
    assert publish.await_count >= 2
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_rebuild_task_marks_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.domain import EnvironmentStatus
    from app.workers import tasks as task_module

    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.name = "demo"
    environment.namespace_name = "launchpad-env-demo"
    environment.git_branch = "main"
    environment.git_repo_url = "https://github.com/acme/demo.git"
    environment.status = EnvironmentStatus.PROVISIONING
    environment.latest_commit_sha = "a8f9c12"
    environment.owner_id = uuid4()
    environment.workspace_id = None

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

    user_repo = MagicMock()
    user_repo.get_by_id = AsyncMock(return_value=None)

    monkeypatch.setattr(task_module, "_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_module, "EnvironmentRepository", lambda _s: env_repo)
    monkeypatch.setattr(task_module, "DeploymentLogRepository", lambda _s: log_repo)
    monkeypatch.setattr(task_module, "UserRepository", lambda _s: user_repo)
    monkeypatch.setattr(task_module.asyncio, "sleep", AsyncMock())

    provisioner = MagicMock()
    monkeypatch.setattr(task_module, "KubernetesProvisioner", lambda _s: provisioner)

    lock_cm = AsyncMock()
    lock_cm.__aenter__ = AsyncMock(return_value=None)
    lock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.workers.tasks.publish_env_event", new_callable=AsyncMock),
        patch("app.workers.tasks.acquire_state_lock", return_value=lock_cm),
    ):
        await task_module._run_rebuild(str(env_id), "a8f9c12", "corr-1")

    provisioner.rebuild_workload.assert_called_once()
    assert env_repo.update_status.await_count >= 1
    final_call = env_repo.update_status.await_args_list[-1]
    assert final_call.args[1] == EnvironmentStatus.RUNNING


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_to_last_good(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed rebuild restores the previous image and keeps the env RUNNING."""
    from app.models.domain import AuditAction, EnvironmentStatus
    from app.workers import tasks as task_module

    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.name = "demo"
    environment.namespace_name = "launchpad-env-demo"
    environment.git_branch = "main"
    environment.git_repo_url = "https://github.com/acme/demo.git"
    # Previously RUNNING on a known-good image + commit.
    environment.status = EnvironmentStatus.RUNNING
    environment.latest_commit_sha = "good123"
    environment.workload_image = "registry/app:good123"
    environment.enable_postgres = False
    environment.enable_redis = False
    environment.owner_id = uuid4()
    environment.workspace_id = None

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

    user_repo = MagicMock()
    user_repo.get_by_id = AsyncMock(return_value=None)

    monkeypatch.setattr(task_module, "_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_module, "EnvironmentRepository", lambda _s: env_repo)
    monkeypatch.setattr(task_module, "DeploymentLogRepository", lambda _s: log_repo)
    monkeypatch.setattr(task_module, "UserRepository", lambda _s: user_repo)
    monkeypatch.setattr(task_module.asyncio, "sleep", AsyncMock())
    fail_execution = AsyncMock()
    monkeypatch.setattr(task_module, "_fail_execution", fail_execution)
    record_audit = AsyncMock()
    monkeypatch.setattr(task_module, "_record_audit", record_audit)

    provisioner = MagicMock()
    # First call = the failing rebuild; second call = the rollback restore.
    provisioner.rebuild_workload.side_effect = [RuntimeError("rollout timed out"), None]
    monkeypatch.setattr(task_module, "KubernetesProvisioner", lambda _s: provisioner)

    lock_cm = AsyncMock()
    lock_cm.__aenter__ = AsyncMock(return_value=None)
    lock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.workers.tasks.publish_env_event", new_callable=AsyncMock),
        patch("app.workers.tasks.acquire_state_lock", return_value=lock_cm),
    ):
        await task_module._run_rebuild(str(env_id), "bad999", "corr-rollback")

    # Rebuild attempted, then rolled back with the previous image.
    assert provisioner.rebuild_workload.call_count == 2
    restore_call = provisioner.rebuild_workload.call_args_list[-1]
    assert restore_call.kwargs["image"] == "registry/app:good123"
    assert restore_call.kwargs["commit_sha"] == "good123"

    # Env ends RUNNING on the last-good revision, never marked FAILED.
    final_call = env_repo.update_status.await_args_list[-1]
    assert final_call.args[1] == EnvironmentStatus.RUNNING
    assert final_call.kwargs["latest_commit_sha"] == "good123"
    assert final_call.kwargs["workload_image"] == "registry/app:good123"
    fail_execution.assert_not_awaited()

    recorded = {call.kwargs.get("action") for call in record_audit.await_args_list}
    assert AuditAction.REBUILD_ROLLED_BACK in recorded


@pytest.mark.asyncio
async def test_rebuild_failure_without_prior_image_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no known-good state, a failed rebuild still marks the env FAILED."""
    from app.models.domain import EnvironmentStatus
    from app.workers import tasks as task_module

    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.name = "demo"
    environment.namespace_name = "launchpad-env-demo"
    environment.git_branch = "main"
    environment.git_repo_url = "https://github.com/acme/demo.git"
    # Never reached a working state (still provisioning), nothing to restore.
    environment.status = EnvironmentStatus.PROVISIONING
    environment.latest_commit_sha = None
    environment.workload_image = None
    environment.enable_postgres = False
    environment.enable_redis = False
    environment.owner_id = uuid4()
    environment.workspace_id = None

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

    user_repo = MagicMock()
    user_repo.get_by_id = AsyncMock(return_value=None)

    monkeypatch.setattr(task_module, "_session_factory", lambda: session_factory)
    monkeypatch.setattr(task_module, "EnvironmentRepository", lambda _s: env_repo)
    monkeypatch.setattr(task_module, "DeploymentLogRepository", lambda _s: log_repo)
    monkeypatch.setattr(task_module, "UserRepository", lambda _s: user_repo)
    monkeypatch.setattr(task_module.asyncio, "sleep", AsyncMock())
    fail_execution = AsyncMock()
    monkeypatch.setattr(task_module, "_fail_execution", fail_execution)

    provisioner = MagicMock()
    provisioner.rebuild_workload.side_effect = RuntimeError("build failed")
    monkeypatch.setattr(task_module, "KubernetesProvisioner", lambda _s: provisioner)

    lock_cm = AsyncMock()
    lock_cm.__aenter__ = AsyncMock(return_value=None)
    lock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.workers.tasks.publish_env_event", new_callable=AsyncMock),
        patch("app.workers.tasks.acquire_state_lock", return_value=lock_cm),
    ):
        await task_module._run_rebuild(str(env_id), "bad999", "corr-fail")

    # No rollback restore attempted (only the one failing rebuild), env FAILED.
    assert provisioner.rebuild_workload.call_count == 1
    fail_execution.assert_awaited_once()

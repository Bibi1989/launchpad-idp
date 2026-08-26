from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.git_urls import short_commit_sha
from app.services.webhook import GitLabWebhookService


def test_gitlab_verify_signature_accepts_token() -> None:
    secret = "test-webhook-secret"
    service_ok = GitLabWebhookService.verify_signature(
        body=b"{}",
        signature_header=secret,
        secret=secret,
    )
    assert service_ok is True

    service_bad = GitLabWebhookService.verify_signature(
        body=b"{}",
        signature_header="wrong-token",
        secret=secret,
    )
    assert service_bad is False


def test_parse_gitlab_push_event() -> None:
    payload = {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "after": "a8f9c12abcdef0123456789",
        "project": {"path_with_namespace": "acme/demo"},
    }
    details = GitLabWebhookService.parse_push_event(payload)
    assert details is not None
    assert details.repository_full_name == "acme/demo"
    assert details.branch == "main"
    assert details.full_commit_sha == "a8f9c12abcdef0123456789"
    assert details.commit_sha == short_commit_sha("a8f9c12abcdef0123456789")


@pytest.mark.asyncio
async def test_process_gitlab_push_enqueues_rebuild() -> None:
    from app.models.domain import EnvironmentStatus

    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()

    service = GitLabWebhookService(session)
    service._environments = MagicMock()
    service._environments.list_active_for_any_linked_repo = AsyncMock(
        return_value=[environment],
    )
    service._environments.mark_rebuild = AsyncMock(return_value=environment)
    service._logs = MagicMock()
    service._logs.create = AsyncMock()

    payload = {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "after": "a8f9c12abcdef0123456789",
        "project": {"path_with_namespace": "acme/demo"},
    }

    correlation_id = "test-corr"

    with (
        patch("app.services.webhook.publish_env_event", new_callable=AsyncMock) as publish,
        patch("app.services.webhook.is_state_locked", new_callable=AsyncMock, return_value=False),
        patch("app.workers.tasks.enqueue_rebuild_environment") as enqueue,
    ):
        result = await service.process_event(
            event_name="push",
            payload=payload,
            correlation_id=correlation_id,
        )

    assert result.matched_environment_ids == [env_id]
    enqueue.assert_called_once()
    assert publish.await_count >= 1
    session.commit.assert_awaited()


from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.preview_urls import portal_environment_url, stable_pr_preview_url
from app.services.webhook import GitHubWebhookService


def test_stable_pr_preview_url_path_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PREVIEW_PUBLIC_BASE_URL", "http://localhost:3000")
    monkeypatch.delenv("PREVIEW_PR_HOSTNAME_TEMPLATE", raising=False)
    get_settings.cache_clear()
    assert stable_pr_preview_url(42) == "http://localhost:3000/pr/42"
    assert portal_environment_url("abc").endswith("/environments/abc")
    get_settings.cache_clear()


def test_stable_pr_preview_url_hostname_template(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(
        "PREVIEW_PR_HOSTNAME_TEMPLATE",
        "https://pr-{pr}.preview.example.com",
    )
    get_settings.cache_clear()
    assert stable_pr_preview_url(7) == "https://pr-7.preview.example.com"

    # Test automatic https:// prefix when template has no scheme
    monkeypatch.setenv(
        "PREVIEW_PR_HOSTNAME_TEMPLATE",
        "pr-{pr}.preview.example.com",
    )
    get_settings.cache_clear()
    assert stable_pr_preview_url(12) == "https://pr-12.preview.example.com"
    get_settings.cache_clear()


def test_parse_pull_request_event() -> None:
    payload = {
        "action": "closed",
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": 12,
            "html_url": "https://github.com/acme/demo/pull/12",
            "merged": True,
            "head": {"ref": "feature/x", "sha": "a8f9c12abcdef0123456789"},
        },
    }
    details = GitHubWebhookService.parse_pull_request_event(payload)
    assert details is not None
    assert details.pr_number == 12
    assert details.merged is True
    assert details.short_sha == "a8f9c12"


@pytest.mark.asyncio
async def test_process_pull_request_closed_enqueues_teardown() -> None:
    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None
    environment.status = MagicMock()
    environment.status.__eq__ = lambda self, other: False
    # Make status comparisons work with enum-like membership
    from app.models.domain import EnvironmentStatus

    environment.status = EnvironmentStatus.RUNNING

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    service = GitHubWebhookService(session)
    service._environments = MagicMock()
    service._environments.list_active_for_repo_pr = AsyncMock(return_value=[environment])
    service._environments.update_status = AsyncMock()
    service._logs = MagicMock()
    service._logs.create = AsyncMock()

    payload = {
        "action": "closed",
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": 12,
            "html_url": "https://github.com/acme/demo/pull/12",
            "merged": True,
            "head": {"ref": "feature/x", "sha": "a8f9c12abcdef0123456789"},
        },
    }

    with (
        patch("app.services.webhook.publish_env_event", new_callable=AsyncMock),
        patch("app.services.webhook.is_state_locked", new_callable=AsyncMock, return_value=False),
        patch("app.workers.tasks.enqueue_teardown_environment") as enqueue,
        patch("app.services.audit.AuditService.record", new_callable=AsyncMock),
    ):
        result = await service.process_event(
            event_name="pull_request",
            payload=payload,
            correlation_id="corr-1",
        )

    assert result.accepted
    assert result.matched_environment_ids == [env_id]
    enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_process_pull_request_synchronize_rebuilds() -> None:
    env_id = uuid4()
    environment = MagicMock()
    environment.id = env_id
    environment.workspace_id = None
    environment.github_pr_url = None

    session = AsyncMock()
    session.commit = AsyncMock()
    service = GitHubWebhookService(session)
    service._environments = MagicMock()
    service._environments.list_active_for_repo_pr = AsyncMock(return_value=[environment])
    service._environments.mark_rebuild = AsyncMock(return_value=environment)
    service._logs = MagicMock()
    service._logs.create = AsyncMock()

    payload = {
        "action": "synchronize",
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": 9,
            "html_url": "https://github.com/acme/demo/pull/9",
            "merged": False,
            "head": {"ref": "feature/y", "sha": "bbbbbbb0123456789abcdef"},
        },
    }

    with (
        patch("app.services.webhook.publish_env_event", new_callable=AsyncMock),
        patch("app.services.webhook.is_state_locked", new_callable=AsyncMock, return_value=False),
        patch("app.workers.tasks.enqueue_rebuild_environment") as enqueue,
        patch("app.services.audit.AuditService.record", new_callable=AsyncMock),
    ):
        result = await service.process_event(
            event_name="pull_request",
            payload=payload,
            correlation_id="corr-2",
        )

    assert result.matched_environment_ids == [env_id]
    enqueue.assert_called_once()

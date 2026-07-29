"""Governance helpers: TTL extend schema + soft concurrency settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.domain import EnvironmentStatus
from app.schemas.environment import (
    EnvironmentExtendRequest,
    EnvironmentPromoteRequest,
    EnvironmentRead,
    PreviewProvider,
)


def test_extend_request_defaults() -> None:
    payload = EnvironmentExtendRequest()
    assert payload.hours is None
    payload = EnvironmentExtendRequest(hours=8)
    assert payload.hours == 8


def test_promote_rejects_local() -> None:
    with pytest.raises(ValidationError):
        EnvironmentPromoteRequest(provider=PreviewProvider.LOCAL)


def test_environment_read_app_ready_and_ttl() -> None:
    now = datetime.now(UTC)
    payload = EnvironmentRead(
        id=uuid4(),
        owner_id=uuid4(),
        workspace_id=None,
        name="demo",
        git_branch="main",
        git_repo_url="https://github.com/example/app.git",
        status=EnvironmentStatus.RUNNING,
        namespace_name="launchpad-env-demo",
        preview_url="http://127.0.0.1:30080",
        template_id="hello-web",
        provider="local",
        workload_image="nginx:1.27-alpine",
        node_port=30080,
        ttl_expires_at=now + timedelta(hours=1),
        cost_estimate_hourly=Decimal("0.0000"),
        error_message=None,
        created_at=now - timedelta(hours=1),
        updated_at=now,
    )
    assert payload.app_ready is True
    assert payload.time_remaining_seconds > 0


def test_governance_settings_defaults() -> None:
    assert Settings.model_fields["max_concurrent_environments"].default == 5
    assert Settings.model_fields["ttl_extend_hours_default"].default == 8
    assert Settings.model_fields["ttl_warning_hours"].default == 2
    cap = Settings.model_fields["preview_soft_cost_cap"].default
    assert cap is not None and Decimal(str(cap)) > 0

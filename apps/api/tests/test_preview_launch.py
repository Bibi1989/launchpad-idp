"""Tests for one-click preview templates and EnvironmentRead runtime fields."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.domain import EnvironmentStatus
from app.schemas.environment import (
    EnvironmentRead,
    PreviewLaunchRequest,
    PreviewProvider,
)
from app.services.preview_templates import get_preview_template, list_preview_templates


def test_preview_templates_catalog() -> None:
    items = list_preview_templates()
    assert len(items) >= 3
    hello = get_preview_template("hello-web")
    assert hello.git_branch in {"main", "master"}
    assert hello.default_ttl_hours > 0
    assert "nginx" in hello.workload_image
    assert hello.git_repo_url.startswith("https://github.com/")
    echo = get_preview_template("node-api")
    assert "http-echo" in echo.workload_image


def test_preview_launch_local_skips_credentials() -> None:
    payload = PreviewLaunchRequest(
        name="kind-demo",
        template_id="hello-web",
        provider=PreviewProvider.LOCAL,
    )
    assert payload.provider == PreviewProvider.LOCAL
    assert payload.credentials.gcp_sa_key_json is None


def test_preview_launch_custom_repo() -> None:
    payload = PreviewLaunchRequest(
        name="my-feature",
        git_repo_url="https://github.com/acme/app.git",
        git_branch="feat/x",
        provider=PreviewProvider.LOCAL,
    )
    assert payload.template_id is None
    assert payload.git_repo_url == "https://github.com/acme/app.git"
    assert payload.git_branch == "feat/x"


def test_preview_launch_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        PreviewLaunchRequest(
            name="bad",
            provider=PreviewProvider.LOCAL,
        )
    with pytest.raises(ValidationError):
        PreviewLaunchRequest(
            name="bad",
            template_id="hello-web",
            git_repo_url="https://github.com/acme/app.git",
            git_branch="main",
            provider=PreviewProvider.LOCAL,
        )
    with pytest.raises(ValidationError):
        PreviewLaunchRequest(
            name="bad",
            template_id="hello-web",
            workspace_id=uuid4(),
            provider=PreviewProvider.LOCAL,
        )


def test_preview_launch_workspace_only() -> None:
    workspace_id = uuid4()
    payload = PreviewLaunchRequest(
        name="ws-demo",
        workspace_id=workspace_id,
        provider=PreviewProvider.LOCAL,
    )
    assert payload.workspace_id == workspace_id
    assert payload.template_id is None
    assert payload.git_repo_url is None


def test_preview_launch_gcp_requires_credentials() -> None:
    with pytest.raises(ValidationError):
        PreviewLaunchRequest(
            name="cloud-demo",
            template_id="hello-web",
            provider=PreviewProvider.GCP,
        )


def test_preview_launch_with_workspace_skips_credentials() -> None:
    payload = PreviewLaunchRequest(
        name="cloud-linked",
        workspace_id=uuid4(),
        provider=PreviewProvider.GCP,
    )
    assert payload.workspace_id is not None
    assert payload.credentials.gcp_sa_key_json is None


def test_preview_launch_accepts_custom_workload_image() -> None:
    payload = PreviewLaunchRequest(
        name="image-demo",
        template_id="hello-web",
        provider=PreviewProvider.LOCAL,
        workload_image="ghcr.io/acme/demo:sha-123",
    )
    assert payload.workload_image == "ghcr.io/acme/demo:sha-123"


def test_environment_read_computes_cost_and_ttl() -> None:
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
        preview_url="http://localhost:3000/p/demo",
        template_id="hello-web",
        ttl_expires_at=now + timedelta(hours=2),
        cost_estimate_hourly=Decimal("0.4200"),
        error_message=None,
        created_at=now - timedelta(hours=1),
        updated_at=now,
    )
    assert payload.cost_accrued >= Decimal("0.4")
    assert payload.time_remaining_seconds > 7000

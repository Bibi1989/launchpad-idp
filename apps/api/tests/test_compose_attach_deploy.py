"""Tests for Compose and attach preview executors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.schemas.cloud import (
    KubernetesPackaging,
    RunningInstanceConfig,
    RunningInstanceKind,
)
from app.services.attach_deploy import AttachDeployError, deploy_attach
from app.services.compose_deploy import (
    ComposeDeployError,
    compose_project_name,
    deploy_compose,
    find_compose_file,
    teardown_compose,
)
from app.services.runtime_mode import coerce_wizard_snapshot


def test_find_compose_file(tmp_path: Path) -> None:
    assert find_compose_file(tmp_path) is None
    target = tmp_path / "docker-compose.yml"
    target.write_text("services:\n  app:\n    image: nginx\n", encoding="utf-8")
    assert find_compose_file(tmp_path) == target


def test_compose_project_name_sanitizes() -> None:
    name = compose_project_name(
        namespace="launchpad-env-abc123",
        environment_id="11111111-2222-3333-4444-555555555555",
    )
    assert name.startswith("launchpad-env")
    assert " " not in name


def test_deploy_compose_requires_file(tmp_path: Path) -> None:
    with pytest.raises(ComposeDeployError, match="No docker-compose"):
        deploy_compose(
            workspace_root=tmp_path,
            namespace="launchpad-env-x",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
        )


def test_deploy_compose_simulates_without_docker(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_text(
        'services:\n  app:\n    image: nginx\n    ports:\n      - "9090:80"\n',
        encoding="utf-8",
    )
    settings = Settings.model_construct(preview_node_host="127.0.0.1")
    with patch("app.services.compose_deploy.docker_compose_available", return_value=False):
        resources = deploy_compose(
            workspace_root=tmp_path,
            namespace="launchpad-env-x",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            settings=settings,
        )
    assert resources.simulated is True
    assert resources.node_port == 9090
    assert resources.preview_url == "http://127.0.0.1:9090"


def test_deploy_compose_up_success(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  app:\n    image: nginx\n", encoding="utf-8")

    up = MagicMock(returncode=0, stdout="", stderr="")
    ps = MagicMock(
        returncode=0,
        stdout='{"Publishers":[{"PublishedPort":8080,"TargetPort":8080}]}\n',
        stderr="",
    )
    settings = Settings.model_construct(
        preview_node_host="127.0.0.1",
        kubernetes_ready_timeout_seconds=30,
    )

    with (
        patch("app.services.compose_deploy.docker_compose_available", return_value=True),
        patch("app.services.compose_deploy._run_compose", side_effect=[up, ps]) as run,
    ):
        resources = deploy_compose(
            workspace_root=tmp_path,
            namespace="launchpad-env-x",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            settings=settings,
        )

    assert resources.simulated is False
    assert resources.node_port == 8080
    assert resources.preview_url == "http://127.0.0.1:8080"
    assert run.call_count == 2


def test_teardown_compose_without_docker(tmp_path: Path) -> None:
    with patch("app.services.compose_deploy.docker_compose_available", return_value=False):
        teardown_compose(
            workspace_root=tmp_path,
            namespace="launchpad-env-x",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


def test_attach_endpoint() -> None:
    resources = deploy_attach(
        namespace="ns",
        environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="demo",
        git_branch="main",
        git_repo_url="https://example.com/repo.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.ENDPOINT,
            endpoint_url="https://app.example.com",
        ),
    )
    assert resources.preview_url == "https://app.example.com"
    assert resources.created_workload is True


def test_attach_serverless_requires_url() -> None:
    with pytest.raises(AttachDeployError, match="endpoint_url"):
        deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.SERVERLESS),
        )


def test_attach_kube_uses_provisioner() -> None:
    fake = MagicMock()
    fake.provision.return_value = MagicMock(
        namespace="ns",
        preview_url="http://127.0.0.1:30080",
        labels={},
        created_workload=True,
    )
    with patch("app.services.attach_deploy.KubernetesProvisioner", return_value=fake):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.KUBE_CONTEXT,
                kube_context="kind-launchpad",
            ),
            packaging=KubernetesPackaging.NONE,
        )
    assert resources.preview_url == "http://127.0.0.1:30080"
    fake.provision.assert_called_once()


def test_coerce_wizard_snapshot_defaults_runtime() -> None:
    coerced = coerce_wizard_snapshot({"name": "legacy"})
    assert coerced["runtime_mode"] == "kubernetes"
    assert isinstance(coerced["running_instance"], dict)

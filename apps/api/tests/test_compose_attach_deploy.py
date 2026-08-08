"""Tests for Compose and attach preview executors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.schemas.cloud import (
    RunningInstanceConfig,
    RunningInstanceKind,
)
from app.services.attach_deploy import AttachDeployError, deploy_attach, teardown_attach
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


def test_attach_vm_link_only_override() -> None:
    settings = Settings.model_construct(default_workload_image="nginx:latest")
    resources = deploy_attach(
        namespace="ns",
        environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="demo",
        git_branch="main",
        git_repo_url="https://example.com/repo.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            preview_url_override="https://app.example.com",
        ),
        settings=settings,
    )
    assert resources.preview_url == "https://app.example.com"
    assert resources.created_workload is True


def test_attach_legacy_endpoint_coerces_to_vm() -> None:
    settings = Settings.model_construct(default_workload_image="nginx:latest")
    cfg = RunningInstanceConfig.model_validate(
        {"kind": "endpoint", "endpoint_url": "https://legacy.example.com"}
    )
    assert cfg.kind == RunningInstanceKind.VM
    assert cfg.preview_url_override == "https://legacy.example.com"
    resources = deploy_attach(
        namespace="ns",
        environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="demo",
        git_branch="main",
        git_repo_url="https://example.com/repo.git",
        ttl_expires_at="2099-01-01T00:00:00+00:00",
        running_instance=cfg,
        settings=settings,
    )
    assert resources.preview_url == "https://legacy.example.com"


def test_attach_vm_requires_host() -> None:
    settings = Settings.model_construct(default_workload_image="nginx:latest")
    with pytest.raises(AttachDeployError, match="host"):
        deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
            settings=settings,
        )


def test_attach_serverless_simulates_without_gcloud() -> None:
    settings = Settings.model_construct(default_workload_image="nginx:latest")
    with patch("app.services.attach_deploy.shutil.which", return_value=None):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.SERVERLESS,
                service_name="my-svc",
                region="europe-west1",
            ),
            settings=settings,
        )
    assert resources.simulated is True
    assert resources.created_workload is True
    assert "my-svc" in (resources.preview_url or "")
    assert "europe-west1" in (resources.preview_url or "")


def test_attach_local_machine_simulates_without_docker() -> None:
    settings = Settings.model_construct(
        default_workload_image="nginx:latest",
        preview_node_host="127.0.0.1",
    )
    with patch("app.services.attach_deploy._docker_available", return_value=False):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.LOCAL_MACHINE,
                listen_port=9090,
            ),
            settings=settings,
        )
    assert resources.simulated is True
    assert resources.node_port == 9090
    assert resources.preview_url == "http://127.0.0.1:9090"


def test_teardown_attach_local() -> None:
    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch("app.services.attach_deploy._run") as run,
    ):
        teardown_attach(
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    run.assert_called_once()
    assert run.call_args.args[0][0] == "docker"


def test_coerce_wizard_snapshot_defaults_runtime() -> None:
    coerced = coerce_wizard_snapshot({"name": "legacy"})
    assert coerced["runtime_mode"] == "kubernetes"
    assert isinstance(coerced["running_instance"], dict)

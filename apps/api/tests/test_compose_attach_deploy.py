"""Tests for Compose and attach preview executors."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.schemas.cloud import (
    InstanceCodeSource,
    InstanceProcessStrategy,
    InstanceReverseProxy,
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


def test_repair_compose_rewrites_broken_client_scaffold(tmp_path: Path) -> None:
    from app.services.compose_deploy import repair_compose_for_scaffolded_apps

    app_dir = tmp_path / "apps" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    (app_dir / "Dockerfile").write_text(
        "FROM node:22-alpine\nEXPOSE 8080\n"
        'HEALTHCHECK CMD wget -qO- http://127.0.0.1:8080/health || exit 1\n',
        encoding="utf-8",
    )
    (tmp_path / "dockers").mkdir()
    (tmp_path / "dockers" / "Dockerfile.app").write_text(
        "FROM node:22-alpine\nCOPY package.json ./\n",
        encoding="utf-8",
    )
    broken = tmp_path / "docker-compose.yml"
    broken.write_text(
        "services:\n"
        "  app:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: dockers/Dockerfile.app\n"
        "    image: app:latest\n"
        "    ports:\n"
        '      - "8080:8080"\n',
        encoding="utf-8",
    )

    repaired = repair_compose_for_scaffolded_apps(tmp_path)
    assert repaired == broken
    text = broken.read_text(encoding="utf-8")
    assert "context: apps/app" in text
    assert "dockerfile: Dockerfile" in text
    assert "context: ." not in text
    assert "dockers/Dockerfile.app" not in text


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


def test_prepare_preview_compose_remaps_busy_ports(tmp_path: Path) -> None:
    from app.services.compose_deploy import PREVIEW_COMPOSE_FILENAME, prepare_preview_compose

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  app:\n"
        "    image: nginx\n"
        "    container_name: app\n"
        "    ports:\n"
        '      - "8080:8080"\n'
        "  db:\n"
        "    image: postgres:16-alpine\n"
        "    ports:\n"
        '      - "5432:5432"\n',
        encoding="utf-8",
    )
    with (
        patch(
            "app.services.compose_deploy.list_docker_published_host_ports",
            return_value={8080, 5432},
        ),
        patch(
            "app.services.compose_deploy.is_host_port_available",
            side_effect=lambda port, docker_ports=None: port not in {8080, 5432},
        ),
    ):
        preview, notes = prepare_preview_compose(compose)
    assert preview.name == PREVIEW_COMPOSE_FILENAME
    text = preview.read_text(encoding="utf-8")
    assert "container_name" not in text
    assert "8080:8080" not in text
    assert "5432:5432" not in text
    assert "8081:8080" in text
    assert "5433:5432" in text
    assert any("8080" in note and "8081" in note for note in notes)
    assert any("5432" in note and "5433" in note for note in notes)


def test_first_published_port_prefers_frontend_service(tmp_path: Path) -> None:
    from app.services.compose_deploy import _first_published_port

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  api-server:\n"
        "    image: api\n"
        "    labels:\n"
        "      - launchpad.io/app-kind=backend\n"
        "      - launchpad.io/preview-target=false\n"
        "    x-launchpad:\n"
        "      app_kind: backend\n"
        "      preview_target: false\n"
        "    ports:\n"
        '      - "8080:8080"\n'
        "  web-ui:\n"
        "    image: web\n"
        "    labels:\n"
        "      - launchpad.io/app-kind=frontend\n"
        "      - launchpad.io/preview-target=true\n"
        "    x-launchpad:\n"
        "      app_kind: frontend\n"
        "      preview_target: true\n"
        "    ports:\n"
        '      - "3000:3000"\n'
        "  postgres:\n"
        "    image: postgres:16-alpine\n"
        "    ports:\n"
        '      - "5432:5432"\n',
        encoding="utf-8",
    )
    ps = MagicMock(
        returncode=0,
        stdout=(
            '{"Service":"api-server","Publishers":[{"PublishedPort":8080,"TargetPort":8080}]}\n'
            '{"Service":"web-ui","Publishers":[{"PublishedPort":3000,"TargetPort":3000}]}\n'
            '{"Service":"postgres","Publishers":[{"PublishedPort":5432,"TargetPort":5432}]}\n'
        ),
        stderr="",
    )
    with patch("app.services.compose_deploy._run_compose", return_value=ps):
        port = _first_published_port(
            project="demo",
            compose_file=compose,
            cwd=tmp_path,
        )
    assert port == 3000


def test_attach_prefers_frontend_dockerfile(tmp_path: Path) -> None:
    from app.services.attach_deploy import _find_workspace_dockerfile

    api = tmp_path / "apps" / "api-server"
    web = tmp_path / "apps" / "web-ui"
    api.mkdir(parents=True)
    web.mkdir(parents=True)
    (api / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
    (web / "Dockerfile").write_text("FROM node:22-alpine\n", encoding="utf-8")
    found = _find_workspace_dockerfile(tmp_path)
    assert found is not None
    _df, context = found
    assert context.name == "web-ui"


def test_deploy_compose_up_success(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        'services:\n  app:\n    image: nginx\n    ports:\n      - "8080:8080"\n',
        encoding="utf-8",
    )

    down_preview = MagicMock(returncode=0, stdout="", stderr="")
    down_project = MagicMock(returncode=0, stdout="", stderr="")
    up = MagicMock(returncode=0, stdout="", stderr="")
    ps = MagicMock(
        returncode=0,
        stdout='{"Publishers":[{"PublishedPort":8081,"TargetPort":8080}]}\n',
        stderr="",
    )
    settings = Settings.model_construct(
        preview_node_host="127.0.0.1",
        kubernetes_ready_timeout_seconds=30,
    )

    with (
        patch("app.services.compose_deploy.docker_compose_available", return_value=True),
        patch(
            "app.services.compose_deploy.list_docker_published_host_ports",
            return_value={8080},
        ),
        patch(
            "app.services.compose_deploy.is_host_port_available",
            side_effect=lambda port, docker_ports=None: port != 8080,
        ),
        patch(
            "app.services.compose_deploy._run_compose",
            side_effect=[down_preview, down_project, up, ps],
        ) as run,
    ):
        resources = deploy_compose(
            workspace_root=tmp_path,
            namespace="launchpad-env-x",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            settings=settings,
        )

    assert resources.simulated is False
    assert resources.node_port == 8081
    assert resources.preview_url == "http://127.0.0.1:8081"
    assert resources.notice is not None
    assert "8080" in resources.notice and "8081" in resources.notice
    assert run.call_count == 4
    preview_path = tmp_path / "docker-compose.launchpad-preview.yml"
    assert preview_path.is_file()
    up_args = run.call_args_list[2].args[0]
    assert str(preview_path) in up_args


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


def test_attach_vm_without_host_falls_back_to_local_docker() -> None:
    # A VM with no host and a local provider has nowhere to attach and no cloud to
    # create it. Instead of failing the preview, it runs via local Docker.
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
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM, listen_port=8080),
            settings=settings,
        )
    # Fell back to the local Docker preview path instead of raising.
    assert resources.simulated is True
    assert resources.preview_url == "http://127.0.0.1:8080"


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
        default_workload_image="",
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


def test_attach_pm2_cloud_vm_skips_docker_image_push(tmp_path: Path) -> None:
    """Instance mode with pm2 must not build/push to Artifact Registry."""
    from app.schemas.cloud import InstanceCodeSource, InstanceProcessStrategy

    web = tmp_path / "apps" / "web-ui"
    web.mkdir(parents=True)
    (web / "package.json").write_text(
        '{"scripts":{"start":"node server.js"}}',
        encoding="utf-8",
    )
    settings = Settings.model_construct(
        default_workload_image="nginx:latest",
        preview_node_host="127.0.0.1",
    )
    remote_cmds: list[str] = []

    def fake_remote_shell(**kwargs):
        remote_cmds.append(str(kwargs.get("command") or ""))

    def boom_resolve(*_a, **_k):
        raise AssertionError("resolve_instance_image must not run for pm2 cloud VM")

    with (
        patch("app.services.attach_deploy.resolve_instance_image", side_effect=boom_resolve),
        patch("app.services.attach_deploy._wait_for_vm_ssh", return_value=None),
        patch("app.services.attach_deploy._sync_workspace_over_ssh", return_value=None),
        patch("app.services.attach_deploy._remote_shell", side_effect=fake_remote_shell),
        patch(
            "app.services.cloud_instance_compute.provision_cloud_vm",
            side_effect=lambda **kw: kw["running_instance"].model_copy(
                update={"host": "1.2.3.4", "service_name": "lp-demo", "region": "us-central1-a"}
            ),
        ),
    ):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://github.com/acme/app.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                process_strategy=InstanceProcessStrategy.PM2,
                code_source=InstanceCodeSource.SSH,
                listen_port=3000,
                service_name="lp-demo",
                region="us-central1-a",
            ),
            workspace_root=tmp_path,
            settings=settings,
            cloud_provider="gcp",
        )
    assert resources.preview_url == "http://1.2.3.4:3000"
    assert resources.image is None
    joined = "\n".join(remote_cmds)
    assert "deb.nodesource.com/setup_20.x" in joined
    assert "npm install -g pm2" in joined or "sudo npm install -g pm2" in joined
    assert "apps/web-ui" in joined
    assert "pm2" in joined


def test_resolve_app_workdir_prefers_frontend(tmp_path: Path) -> None:
    from app.services.attach_deploy import _resolve_app_workdir_rel

    (tmp_path / "apps" / "api-server").mkdir(parents=True)
    (tmp_path / "apps" / "web-ui").mkdir(parents=True)
    (tmp_path / "apps" / "api-server" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "apps" / "web-ui" / "package.json").write_text("{}", encoding="utf-8")
    assert _resolve_app_workdir_rel(tmp_path) == "apps/web-ui"


def test_attach_pm2_local_machine_coerces_to_docker_for_preview() -> None:
    # A pm2/systemd process strategy must not fail the one-click preview: Live Launch
    # runs the app via Docker and leaves the pm2 scaffolds for a real host deploy.
    settings = Settings.model_construct(
        default_workload_image="",
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
                process_strategy=InstanceProcessStrategy.PM2,
            ),
            settings=settings,
        )
    # Coerced to the Docker preview path instead of raising AttachDeployError.
    assert resources.simulated is True
    assert resources.preview_url == "http://127.0.0.1:9090"


def test_attach_multi_service_exposes_frontend_and_optional_backend(tmp_path: Path) -> None:
    from app.schemas.cloud import ContainerServiceSpec

    web = tmp_path / "apps" / "web-ui"
    api = tmp_path / "apps" / "api-server"
    web.mkdir(parents=True)
    api.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM node:20-alpine\nEXPOSE 3000\n", encoding="utf-8")
    (api / "Dockerfile").write_text("FROM node:20-alpine\nEXPOSE 8080\n", encoding="utf-8")

    settings = Settings.model_construct(
        default_workload_image="node:20-alpine",
        preview_node_host="127.0.0.1",
    )
    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, timeout: float, check: bool = True):
        _ = timeout
        run_calls.append(cmd)
        if cmd[:2] == ["docker", "build"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return MagicMock(returncode=0, stdout="[]", stderr="")
        if cmd[:2] == ["docker", "run"]:
            return MagicMock(returncode=0, stdout="cid\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch(
            "app.services.attach_deploy.list_docker_published_host_ports",
            return_value=set(),
        ),
        patch(
            "app.services.compose_deploy.is_host_port_available",
            return_value=True,
        ),
        patch("app.services.attach_deploy._run", side_effect=fake_run),
    ):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.LOCAL_MACHINE,
                listen_port=8090,
            ),
            settings=settings,
            workspace_root=tmp_path,
            services=[
                ContainerServiceSpec(
                    name="web-ui",
                    app_kind="frontend",
                    listen_port=3000,
                    expose_preview=True,
                ),
                ContainerServiceSpec(
                    name="api-server",
                    app_kind="backend",
                    listen_port=8080,
                    expose_preview=True,
                ),
            ],
        )

    assert resources.preview_url == "http://127.0.0.1:8090"
    assert resources.node_port == 8090
    assert len(resources.preview_endpoints) == 2
    names = {e["name"] for e in resources.preview_endpoints}
    assert names == {"web-ui", "api-server"}
    run_cmds = [" ".join(c) for c in run_calls if c[:2] == ["docker", "run"]]
    assert any("NEXT_PUBLIC_API_URL=http://api-server:8080" in c for c in run_cmds)
    assert any("-p 8090:3000" in c for c in run_cmds)
    assert any("-p 8080:8080" in c for c in run_cmds)


def test_attach_local_machine_publishes_user_host_port_to_expose(tmp_path: Path) -> None:
    """User listen_port is host publish; Dockerfile EXPOSE is container port."""
    (tmp_path / "Dockerfile").write_text(
        "FROM node:20-alpine\nEXPOSE 3000\n",
        encoding="utf-8",
    )
    settings = Settings.model_construct(
        default_workload_image="node:20-alpine",
        preview_node_host="127.0.0.1",
    )
    inspect_ok = MagicMock(returncode=0, stdout="[]", stderr="")
    rm_ok = MagicMock(returncode=0, stdout="", stderr="")
    run_ok = MagicMock(returncode=0, stdout="cid\n", stderr="")

    def fake_run(cmd: list[str], *, timeout: float, check: bool = True):
        _ = timeout
        if cmd[:3] == ["docker", "image", "inspect"]:
            return inspect_ok
        if cmd[:3] == ["docker", "rm", "-f"]:
            return rm_ok
        if cmd[:2] == ["docker", "run"]:
            publish = cmd[cmd.index("-p") + 1]
            assert publish == "8090:3000"
            assert "PORT=3000" in cmd
            return run_ok
        if check:
            raise AttachDeployError(f"unexpected cmd: {cmd}")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch(
            "app.services.attach_deploy.list_docker_published_host_ports",
            return_value=set(),
        ),
        patch(
            "app.services.compose_deploy.is_host_port_available",
            return_value=True,
        ),
        patch("app.services.attach_deploy._run", side_effect=fake_run),
    ):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.LOCAL_MACHINE,
                listen_port=8090,
            ),
            settings=settings,
            workspace_root=tmp_path,
        )

    assert resources.node_port == 8090
    assert resources.preview_url == "http://127.0.0.1:8090"


def test_attach_local_machine_remaps_busy_host_port() -> None:
    settings = Settings.model_construct(
        default_workload_image="nginx:alpine",
        preview_node_host="127.0.0.1",
    )
    inspect_ok = MagicMock(returncode=0, stdout="[]", stderr="")
    rm_ok = MagicMock(returncode=0, stdout="", stderr="")
    run_ok = MagicMock(returncode=0, stdout="cid\n", stderr="")

    def fake_run(cmd: list[str], *, timeout: float, check: bool = True):
        _ = timeout
        if cmd[:3] == ["docker", "image", "inspect"]:
            return inspect_ok
        if cmd[:3] == ["docker", "rm", "-f"]:
            return rm_ok
        if cmd[:2] == ["docker", "run"]:
            assert "-p" in cmd
            publish = cmd[cmd.index("-p") + 1]
            assert publish == "8081:8080"
            assert f"PORT=8080" in cmd
            return run_ok
        if check:
            raise AttachDeployError(f"unexpected cmd: {cmd}")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch(
            "app.services.attach_deploy.list_docker_published_host_ports",
            return_value={8080},
        ),
        patch(
            "app.services.compose_deploy.is_host_port_available",
            side_effect=lambda port, docker_ports=None: port != 8080,
        ),
        patch("app.services.attach_deploy._run", side_effect=fake_run),
    ):
        resources = deploy_attach(
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            name="demo",
            git_branch="main",
            git_repo_url="https://example.com/repo.git",
            ttl_expires_at="2099-01-01T00:00:00+00:00",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.LOCAL_MACHINE,
                listen_port=8080,
            ),
            settings=settings,
        )

    assert resources.simulated is False
    assert resources.node_port == 8081
    assert resources.preview_url == "http://127.0.0.1:8081"
    assert resources.notice is not None
    assert "8080" in resources.notice and "8081" in resources.notice


def test_resolve_instance_image_from_workspace_dockerfile(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "app"
    app.mkdir(parents=True)
    (app / "Dockerfile").write_text("FROM nginx:alpine\n", encoding="utf-8")
    settings = Settings.model_construct(default_workload_image="")
    with patch("app.services.attach_deploy._docker_available", return_value=False):
        from app.services.attach_deploy import resolve_instance_image

        image = resolve_instance_image(
            image=None,
            workspace_root=tmp_path,
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            settings=settings,
        )
    assert image.startswith("lp-ws-")


def test_resolve_instance_image_skips_default_when_workspace_dockerfile(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "app"
    app.mkdir(parents=True)
    (app / "Dockerfile").write_text("FROM busybox\n", encoding="utf-8")
    settings = Settings.model_construct(default_workload_image="nginx:1.27-alpine")
    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch("app.services.attach_deploy._run") as run,
    ):
        run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        from app.services.attach_deploy import resolve_instance_image

        image = resolve_instance_image(
            image="nginx:1.27-alpine",
            workspace_root=tmp_path,
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            settings=settings,
        )
    assert image.startswith("lp-ws-")
    assert run.call_args.args[0][0:2] == ["docker", "build"]



def test_find_workspace_dockerfile_prefers_web_over_api(tmp_path) -> None:
    from app.services.attach_deploy import _find_workspace_dockerfile

    web = tmp_path / "apps" / "web"
    api = tmp_path / "apps" / "api"
    web.mkdir(parents=True)
    api.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM node\nEXPOSE 3000\n", encoding="utf-8")
    (api / "Dockerfile").write_text("FROM node\nEXPOSE 8080\n", encoding="utf-8")
    found = _find_workspace_dockerfile(tmp_path)
    assert found is not None
    dockerfile, context = found
    assert context.name == "web"
    assert dockerfile.parent.name == "web"


def test_teardown_attach_local() -> None:
    with (
        patch("app.services.attach_deploy._docker_available", return_value=True),
        patch("app.services.attach_deploy._run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        teardown_attach(
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
            namespace="ns",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    cmds = [c.args[0] for c in run.call_args_list]
    assert ["docker", "rm", "-f", "lp-inst-aaaaaaaabbbb"] in cmds
    assert ["docker", "network", "rm", "lp-net-aaaaaaaabbbb"] in cmds


def test_coerce_wizard_snapshot_defaults_runtime() -> None:
    coerced = coerce_wizard_snapshot({"name": "legacy"})
    assert coerced["runtime_mode"] == "kubernetes"


def test_parse_gcp_already_exists_zone() -> None:
    from app.services.cloud_instance_compute import _parse_gcp_already_exists

    detail = (
        "ERROR: (gcloud.compute.instances.create) Could not fetch resource:\n"
        " - The resource 'projects/borrow-493110/zones/europe-west3-a/"
        "instances/web-ui-new-instance-gcp' already exists"
    )
    assert _parse_gcp_already_exists(detail) == (
        "europe-west3-a",
        "web-ui-new-instance-gcp",
    )


def test_provision_gcp_vm_reuses_existing_on_already_exists() -> None:
    from app.services.cloud_instance_compute import _provision_gcp_vm

    existing_row = {
        "name": "web-ui-new-instance-gcp",
        "zone": "projects/p/zones/europe-west3-a",
        "networkInterfaces": [
            {
                "networkIP": "10.0.0.2",
                "accessConfigs": [{"natIP": "34.1.2.3"}],
            }
        ],
    }
    list_results = [
        MagicMock(returncode=0, stdout="[]", stderr=""),
        MagicMock(returncode=0, stdout=json.dumps([existing_row]), stderr=""),
    ]

    def fake_run(cmd, **_kwargs):
        if "firewall-rules" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "instances" in cmd and "list" in cmd:
            return list_results.pop(0)
        if "instances" in cmd and "create" in cmd:
            return MagicMock(
                returncode=1,
                stdout="",
                stderr=(
                    "ERROR: The resource "
                    "'projects/p/zones/europe-west3-a/instances/web-ui-new-instance-gcp' "
                    "already exists"
                ),
            )
        return MagicMock(returncode=1, stdout="", stderr="unexpected")

    with (
        patch("app.services.cloud_instance_compute.shutil.which", return_value="/usr/bin/gcloud"),
        patch("app.services.cloud_instance_compute._run_cmd", side_effect=fake_run),
    ):
        result = _provision_gcp_vm(
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                service_name="web-ui-new-instance-gcp",
                region="europe-west3-a",
            ),
            instance_name="web-ui-new-instance-gcp",
            zone="europe-west3-a",
            listen_port=8080,
            env={"CLOUDSDK_CORE_PROJECT": "borrow-493110"},
            environment_id="c0938f6e-eba6-4b5c-b4b0-d16929cc2b1e",
            environment_name="web-ui-new-instance-gcp",
        )

    assert result.host == "34.1.2.3"
    assert result.region == "europe-west3-a"
    assert result.service_name == "web-ui-new-instance-gcp"


def test_cloud_resource_name_is_unique_per_environment() -> None:
    from app.services.cloud_instance_compute import cloud_resource_name

    a = cloud_resource_name(
        environment_id="aaaaaaaa-1111-1111-1111-111111111111",
        environment_name="demo-gcp",
        base_name="web-ui",
        org_slug="acme",
    )
    b = cloud_resource_name(
        environment_id="bbbbbbbb-2222-2222-2222-222222222222",
        environment_name="demo-gcp",
        base_name="web-ui",
        org_slug="acme",
    )
    c = cloud_resource_name(
        environment_id="aaaaaaaa-1111-1111-1111-111111111111",
        environment_name="demo-gcp",
        base_name="web-ui",
        org_slug="globex",
    )
    assert a != b
    assert a != c
    assert a.startswith("lp-acme-")
    assert c.startswith("lp-globex-")
    assert "aaaaaaaa" in a.replace("-", "")
    assert "bbbbbbbb"[:8] in b.replace("-", "")


def test_resolve_attach_cloud_provider_prefers_workspace_over_local_env() -> None:
    from app.services.attach_deploy import resolve_attach_cloud_provider

    assert (
        resolve_attach_cloud_provider(
            environment_provider="local",
            workspace_provider="gcp",
            wizard_cloud_provider="aws",
            credentials=None,
        )
        == "gcp"
    )
    assert (
        resolve_attach_cloud_provider(
            environment_provider=None,
            workspace_provider=None,
            wizard_cloud_provider="gcp",
            credentials=None,
        )
        == "gcp"
    )


def test_vm_ensure_host_packages_avoids_cloud_init_wait() -> None:
    from app.schemas.cloud import InstanceProcessStrategy
    from app.services.attach_deploy import _vm_ensure_host_packages_script, _wrap_remote_bash

    script = _vm_ensure_host_packages_script(strategy=InstanceProcessStrategy.PM2)
    assert "cloud-init status --wait" not in script
    assert "apt_retry" in script
    assert "host packages already present; skipping apt" in script
    assert "pm2" in script
    wrapped = _wrap_remote_bash("echo ok")
    assert "bash -euo pipefail" in wrapped
    assert "bash -euxo" not in wrapped


def test_vm_ensure_host_packages_rhel_uses_dnf_not_apt() -> None:
    from app.schemas.cloud import InstanceProcessStrategy
    from app.services.attach_deploy import _vm_ensure_host_packages_script

    script = _vm_ensure_host_packages_script(
        strategy=InstanceProcessStrategy.PM2,
        os_family="rhel",
    )
    assert "apt-get" not in script
    assert "dnf install" in script or "sudo $PM install" in script
    assert "rpm.nodesource.com" in script
    assert "host packages already present; skipping package install" in script


def test_native_bootstrap_aws_uses_rhel_packages() -> None:
    from app.schemas.cloud import CloudProvider, InstanceProcessStrategy
    from app.services.attach_deploy import _native_bootstrap_and_start

    script = _native_bootstrap_and_start(
        strategy=InstanceProcessStrategy.PM2,
        app_dir="/opt/launchpad/app",
        workdir_rel=".",
        listen=8080,
        unit="demo",
        start_command="node index.js",
        cloud_provider=CloudProvider.AWS.value,
    )
    assert "apt-get" not in script
    assert "dnf install" in script or "sudo $PM install" in script


def test_gcp_startup_marks_ready_after_docker() -> None:
    import inspect

    from app.services import cloud_instance_compute as cic

    source = inspect.getsource(cic._provision_gcp_vm)
    ready_idx = source.find("touch /var/lib/launchpad/vm-ready")
    docker_idx = source.find("get.docker.com")
    assert ready_idx > 0
    assert docker_idx > 0
    assert docker_idx < ready_idx
def test_teardown_cloud_vm_deletes_by_label_and_name() -> None:
    from app.services.cloud_instance_compute import teardown_cloud_vm

    cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        cmds.append(list(cmd))
        if "list" in cmd and "--filter=labels.launchpad-environment-id=" in " ".join(cmd):
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "name": "web-ui-c0938f6e",
                            "zone": "projects/p/zones/europe-west3-a",
                        }
                    ]
                ),
                stderr="",
            )
        if "list" in cmd and "--filter=name=" in " ".join(cmd):
            return MagicMock(returncode=0, stdout="[]", stderr="")
        if "delete" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("app.services.cloud_instance_compute.shutil.which", return_value="/usr/bin/gcloud"),
        patch("app.services.cloud_instance_compute._run_cmd", side_effect=fake_run),
    ):
        teardown_cloud_vm(
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                service_name="web-ui-new-instance-gcp",
                region="europe-west3-a",
            ),
            environment_id="c0938f6e-eba6-4b5c-b4b0-d16929cc2b1e",
            environment_name="web-ui-new-instance-gcp",
            cloud_provider="gcp",
            credentials=None,
        )

    delete_cmds = [c for c in cmds if "delete" in c]
    assert delete_cmds
    assert any("web-ui-c0938f6e" in c for c in delete_cmds)


# --------------------------------------------------------------------------- #
# Native SSH pm2 / systemd VM deploy (first-class, non-Docker)
# --------------------------------------------------------------------------- #


def test_native_bootstrap_pm2_script() -> None:
    from app.services.attach_deploy import _native_bootstrap_and_start

    script = _native_bootstrap_and_start(
        strategy=InstanceProcessStrategy.PM2,
        app_dir="/opt/launchpad/app",
        workdir_rel=".",
        listen=8080,
        unit="demo",
        start_command="node index.js",
    )
    assert "command -v pm2" in script  # ensures pm2 present
    assert "pm2 start" in script
    assert "pm2 save" in script
    assert "pm2 startup systemd" in script


def test_native_bootstrap_systemd_script() -> None:
    from app.services.attach_deploy import _native_bootstrap_and_start

    script = _native_bootstrap_and_start(
        strategy=InstanceProcessStrategy.SYSTEMD,
        app_dir="/opt/launchpad/app",
        workdir_rel=".",
        listen=9000,
        unit="demo",
        start_command="python app.py",
    )
    assert "/etc/systemd/system/demo.service" in script
    assert "ExecStart=" in script
    assert "systemctl daemon-reload" in script
    assert "systemctl enable --now demo.service" in script


def test_native_reverse_proxy_nginx_and_caddy() -> None:
    from app.services.attach_deploy import _vm_reverse_proxy_script

    nginx = _vm_reverse_proxy_script(proxy=InstanceReverseProxy.NGINX, listen=8080)
    assert "proxy_pass http://127.0.0.1:8080" in nginx
    assert "listen 80 default_server" in nginx

    caddy = _vm_reverse_proxy_script(proxy=InstanceReverseProxy.CADDY, listen=8080)
    assert "reverse_proxy 127.0.0.1:8080" in caddy

    assert _vm_reverse_proxy_script(proxy=InstanceReverseProxy.NONE, listen=8080) == ""


def _run_native_vm_deploy(monkeypatch, *, strategy, reverse_proxy) -> tuple:
    """Drive _deploy_vm_native with all SSH I/O stubbed; return (resources, script)."""
    from app.services import attach_deploy as ad
    from app.services.kubernetes import ProvisionedResources

    captured: dict[str, str] = {}
    monkeypatch.setattr(ad, "_wait_for_vm_ssh", lambda **_: None)
    monkeypatch.setattr(ad, "_wait_for_vm_host_ready", lambda **_: None)
    monkeypatch.setattr(ad, "_clone_repo_on_vm", lambda **_: None)
    monkeypatch.setattr(
        ad, "_remote_shell", lambda **kw: captured.update(command=kw["command"])
    )

    running_instance = RunningInstanceConfig(
        kind=RunningInstanceKind.VM,
        host="10.0.0.5",
        listen_port=8080,
        process_strategy=strategy,
        reverse_proxy=reverse_proxy,
        code_source=InstanceCodeSource.GITHUB,
    )
    resources = ProvisionedResources(namespace="ns", labels={})
    out = ad._deploy_vm_native(
        environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="demo",
        host="10.0.0.5",
        running_instance=running_instance,
        settings=Settings.model_construct(),
        cloud_provider="local",
        credentials=None,
        workspace_root=None,
        git_repo_url="https://github.com/acme/app.git",
        git_branch="main",
        resources=resources,
        override="",
    )
    return out, captured.get("command", "")


def test_deploy_vm_native_pm2_runs_bootstrap_over_ssh(monkeypatch) -> None:
    out, script = _run_native_vm_deploy(
        monkeypatch,
        strategy=InstanceProcessStrategy.PM2,
        reverse_proxy=InstanceReverseProxy.NONE,
    )
    assert "pm2 start" in script
    assert out.created_workload is True
    # No reverse proxy -> app is reachable on its listen port.
    assert out.preview_url == "http://10.0.0.5:8080"


def test_deploy_vm_native_systemd_with_nginx_exposes_port_80(monkeypatch) -> None:
    out, script = _run_native_vm_deploy(
        monkeypatch,
        strategy=InstanceProcessStrategy.SYSTEMD,
        reverse_proxy=InstanceReverseProxy.NGINX,
    )
    assert "/etc/systemd/system/demo.service" in script
    assert "proxy_pass http://127.0.0.1:8080" in script
    # nginx fronts the app -> preview URL is plain :80.
    assert out.preview_url == "http://10.0.0.5"

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
    assert isinstance(coerced["running_instance"], dict)

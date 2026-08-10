"""Tests for running_instance process strategy scaffolds."""

from __future__ import annotations

from pathlib import Path

from app.schemas.cloud import (
    InstanceProcessStrategy,
    InstanceReverseProxy,
    RunningInstanceConfig,
    RunningInstanceKind,
)
from app.services.instance_process_scaffold import (
    ansible_deploy_mode_for_strategy,
    write_instance_process_scaffold,
)


def test_docker_scaffold_writes_run_script(tmp_path: Path) -> None:
    written = write_instance_process_scaffold(
        tmp_path,
        name="demo-app",
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.LOCAL_MACHINE,
            process_strategy=InstanceProcessStrategy.DOCKER,
            reverse_proxy=InstanceReverseProxy.NONE,
            listen_port=9090,
        ),
    )
    assert "infra/instance/docker-run.sh" in written
    assert (tmp_path / "infra/instance/docker-run.sh").is_file()
    assert (tmp_path / "infra/instance/process.json").read_text(encoding="utf-8").count(
        '"process_strategy": "docker"'
    )


def test_pm2_and_nginx_scaffold(tmp_path: Path) -> None:
    written = write_instance_process_scaffold(
        tmp_path,
        name="node-api",
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            process_strategy=InstanceProcessStrategy.PM2,
            reverse_proxy=InstanceReverseProxy.NGINX,
            listen_port=3000,
        ),
        start_command="npm run start",
    )
    assert "ecosystem.config.cjs" in written
    assert "infra/instance/nginx.conf" in written
    eco = (tmp_path / "ecosystem.config.cjs").read_text(encoding="utf-8")
    assert "npm run start" in eco
    assert "3000" in eco
    nginx = (tmp_path / "infra/instance/nginx.conf").read_text(encoding="utf-8")
    assert "127.0.0.1:3000" in nginx


def test_systemd_and_caddy_scaffold(tmp_path: Path) -> None:
    written = write_instance_process_scaffold(
        tmp_path,
        name="go-api",
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            process_strategy=InstanceProcessStrategy.SYSTEMD,
            reverse_proxy=InstanceReverseProxy.CADDY,
        ),
        start_command="./bin/server",
    )
    assert any(p.endswith(".service") for p in written)
    assert "infra/instance/Caddyfile" in written
    assert ansible_deploy_mode_for_strategy(InstanceProcessStrategy.SYSTEMD) == "systemd"
    assert ansible_deploy_mode_for_strategy(InstanceProcessStrategy.PM2) == "pm2"


def test_serverless_skips_scaffold(tmp_path: Path) -> None:
    written = write_instance_process_scaffold(
        tmp_path,
        name="svc",
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.SERVERLESS),
    )
    assert written == []

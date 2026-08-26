"""Tests for scaffold-driven cloud IaC apply and promote routing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.cloud import (
    AnsibleAppDeployMode,
    CloudProvider,
    InstanceProcessStrategy,
    RunningInstanceConfig,
    RunningInstanceKind,
    WorkspaceRuntimeMode,
    WorkspaceWizardConfig,
)
from app.services.cloud_deploy_makefile import write_cloud_deploy_makefile
from app.services.cloud_promote import cloud_config_for_promote, promote_runtime_target
from app.services.iac_apply import parse_preview_fields, run_workspace_iac_apply
from app.services.scaffold_cloud_deploy import (
    ansible_config_for_runtime,
    should_use_scaffold_cloud_deploy,
)
from app.schemas.cloud import CloudCredentials, IaCEngine


def _settings(**kwargs) -> Settings:
    return Settings.model_construct(
        scaffold_cloud_deploy_enabled=True,
        iac_apply_timeout_seconds=60,
        iac_destroy_timeout_seconds=60,
        **kwargs,
    )


def test_parse_preview_fields_prefers_preview_url() -> None:
    fields = parse_preview_fields(
        {
            "preview_url": "https://svc.run.app",
            "public_ip": "1.2.3.4",
        }
    )
    assert fields["preview_url"] == "https://svc.run.app"
    assert fields["public_ip"] == "1.2.3.4"


def test_parse_preview_fields_builds_url_from_ip() -> None:
    fields = parse_preview_fields({"public_ip": "10.0.0.5", "app_listen_port": 3000})
    assert fields["preview_url"] == "http://10.0.0.5:3000"


def test_parse_preview_fields_extracts_gcp_zone_and_name() -> None:
    fields = parse_preview_fields(
        {
            "public_ip": "34.40.75.198",
            "compute_instance_id": (
                "projects/launchpad-504012/zones/europe-west3-a/"
                "instances/lp-new-instance-gcp-vm"
            ),
            "app_listen_port": "8080",
        }
    )
    assert fields["instance_zone"] == "europe-west3-a"
    assert fields["instance_name"] == "lp-new-instance-gcp-vm"


def test_promote_compose_targets_vm() -> None:
    from app.schemas.cloud import GcpCloudConfig, GcpResources, IaCEngine

    source = WorkspaceWizardConfig(
        name="demo",
        iac_engine=IaCEngine.TERRAFORM,
        cloud=GcpCloudConfig(
            provider=CloudProvider.GCP,
            resources=GcpResources(project_id="demo-proj"),
        ),
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.LOCAL_MACHINE),
    )
    assert promote_runtime_target(source) == RunningInstanceKind.VM


def test_gcp_promote_enables_compute_instance() -> None:
    cloud = cloud_config_for_promote(
        CloudProvider.GCP,
        CloudCredentials(),
        target_kind=RunningInstanceKind.VM,
    )
    assert cloud.resources.compute_instance is True
    assert cloud.resources.cloud_run is False


def test_ansible_config_for_compose() -> None:
    cfg = ansible_config_for_runtime(
        source=None,
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            process_strategy=InstanceProcessStrategy.DOCKER,
        ),
    )
    assert cfg.enabled is True
    assert cfg.app_deploy_mode == AnsibleAppDeployMode.DOCKER_COMPOSE


def test_ansible_config_includes_listen_in_ufw() -> None:
    cfg = ansible_config_for_runtime(
        source=None,
        runtime_mode=WorkspaceRuntimeMode.DOCKER_COMPOSE,
        running_instance=RunningInstanceConfig(
            kind=RunningInstanceKind.VM,
            process_strategy=InstanceProcessStrategy.DOCKER,
            listen_port=8080,
        ),
    )
    assert 8080 in cfg.ufw_allow_ports
    assert 22 in cfg.ufw_allow_ports


def test_should_use_scaffold_for_cloud_vm() -> None:
    assert (
        should_use_scaffold_cloud_deploy(
            cloud_provider="gcp",
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
            settings=_settings(),
        )
        is True
    )
    assert (
        should_use_scaffold_cloud_deploy(
            cloud_provider="local",
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
            settings=_settings(),
        )
        is False
    )


def test_makefile_written(tmp_path: Path) -> None:
    files = write_cloud_deploy_makefile(tmp_path, engine=IaCEngine.TERRAFORM)
    assert files == ["Makefile"]
    text = (tmp_path / "Makefile").read_text(encoding="utf-8")
    assert "cloud-up" in text
    assert "configure" in text
    assert "terraform apply" in text


def test_makefile_launchpad_uses_launch_provision(tmp_path: Path) -> None:
    write_cloud_deploy_makefile(tmp_path, engine=IaCEngine.LAUNCHPAD)
    text = (tmp_path / "Makefile").read_text(encoding="utf-8")
    assert "bash infra/launchProvision.sh up" in text
    assert "bash infra/launchProvision.sh down" in text
    assert "bash infra/launchProvision.sh configure" in text


def test_iac_apply_runs_init_apply_output(tmp_path: Path) -> None:
    tf = tmp_path / "infra" / "terraform"
    tf.mkdir(parents=True)
    (tf / "main.tf").write_text('resource "null_resource" "x" {}\n', encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        if cmd[1] == "output":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"public_ip": {"value": "9.9.9.9"}}),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    with (
        patch("app.services.iac_apply.shutil.which", return_value="/usr/bin/terraform"),
        patch("app.services.iac_apply._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_apply(
            root_dir=str(tmp_path),
            engine="terraform",
            credentials=None,
            org_id="o",
            workspace_id="w",
            settings=_settings(),
        )
    assert result.status == "applied"
    assert result.outputs["public_ip"] == "9.9.9.9"
    assert calls[0][:2] == ["terraform", "init"]
    assert calls[1][:2] == ["terraform", "apply"]


def test_teardown_via_scaffold_destroys(tmp_path: Path, monkeypatch) -> None:
    from app.services import scaffold_cloud_deploy as mod
    from app.services.iac_destroy import IaCDestroyResult

    monkeypatch.setattr(
        mod,
        "should_use_scaffold_cloud_deploy",
        lambda **_: True,
    )

    def fake_destroy(**_):
        return IaCDestroyResult("destroyed", "terraform destroy complete")

    monkeypatch.setattr(
        "app.services.iac_destroy.run_workspace_iac_destroy",
        fake_destroy,
    )
    (tmp_path / "infra").mkdir()
    handled, detail = mod.teardown_via_scaffold(
        workspace_root=tmp_path,
        engine="terraform",
        credentials=None,
        org_id="o",
        workspace_id="w",
        cloud_provider="gcp",
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
        settings=_settings(),
        sibling_active_envs=0,
    )
    assert handled is True
    assert "destroy" in detail.lower()


def test_teardown_via_scaffold_skips_shared_workspace(tmp_path: Path) -> None:
    from app.services.scaffold_cloud_deploy import teardown_via_scaffold

    handled, detail = teardown_via_scaffold(
        workspace_root=tmp_path,
        engine="terraform",
        credentials=None,
        org_id="o",
        workspace_id="w",
        cloud_provider="gcp",
        running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
        settings=_settings(),
        sibling_active_envs=2,
    )
    assert handled is False
    assert "other active" in detail

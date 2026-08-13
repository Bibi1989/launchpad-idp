"""Workspace cloud teardown: terraform/pulumi destroy before workspace deletion."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.services.iac_destroy import IaCDestroyResult, run_workspace_iac_destroy


def _settings() -> Settings:
    return Settings.model_construct(iac_destroy_timeout_seconds=60)


def _make_tf_workspace(tmp_path: Path, *, with_state: bool) -> str:
    tf = tmp_path / "infra" / "terraform"
    tf.mkdir(parents=True)
    (tf / "main.tf").write_text("resource \"null_resource\" \"x\" {}\n", encoding="utf-8")
    if with_state:
        (tf / "terraform.tfstate").write_text('{"resources":[{"type":"x"}]}', encoding="utf-8")
    return str(tmp_path)


def test_skips_when_no_terraform_state(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=False)
    result = run_workspace_iac_destroy(
        root_dir=root, engine="terraform", credentials=None,
        org_id="o", workspace_id="w", settings=_settings(),
    )
    assert result.status == "skipped"
    assert "no terraform state" in result.detail
    assert result.ok is True


def test_skips_when_cli_missing(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=True)
    with patch("app.services.iac_destroy.shutil.which", return_value=None):
        result = run_workspace_iac_destroy(
            root_dir=root, engine="terraform", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "skipped"
    assert "not installed" in result.detail


def test_runs_init_then_destroy_on_success(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=True)
    calls: list[list[str]] = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Destroy complete!", stderr="")

    with (
        patch("app.services.iac_destroy.shutil.which", return_value="/usr/bin/terraform"),
        patch("app.services.iac_destroy._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_destroy(
            root_dir=root, engine="terraform", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "destroyed"
    # init before destroy.
    assert calls[0][:2] == ["terraform", "init"]
    assert calls[1][:2] == ["terraform", "destroy"]
    assert "-auto-approve" in calls[1]


def test_opentofu_uses_tofu_cli(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=True)
    calls: list[list[str]] = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("app.services.iac_destroy.shutil.which", return_value="/usr/bin/tofu"),
        patch("app.services.iac_destroy._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_destroy(
            root_dir=root, engine="opentofu", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "destroyed"
    assert all(c[0] == "tofu" for c in calls)


def test_failed_destroy_reports_failure(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=True)

    def fake_run(cmd, **_):
        rc = 0 if cmd[1] == "init" else 1
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="Error: cannot destroy")

    with (
        patch("app.services.iac_destroy.shutil.which", return_value="/usr/bin/terraform"),
        patch("app.services.iac_destroy._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_destroy(
            root_dir=root, engine="terraform", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "failed"
    assert result.ok is False
    assert "cannot destroy" in result.output


def test_timeout_reports_failure(tmp_path: Path) -> None:
    root = _make_tf_workspace(tmp_path, with_state=True)
    with (
        patch("app.services.iac_destroy.shutil.which", return_value="/usr/bin/terraform"),
        patch("app.services.iac_destroy._run", side_effect=subprocess.TimeoutExpired("terraform", 60)),
    ):
        result = run_workspace_iac_destroy(
            root_dir=root, engine="terraform", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "failed"
    assert "timed out" in result.detail


def test_pulumi_no_stack_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "infra" / "pulumi").mkdir(parents=True)

    def fake_run(cmd, **_):
        return subprocess.CompletedProcess(cmd, 255, stdout="", stderr="error: no stack selected")

    with (
        patch("app.services.iac_destroy.shutil.which", return_value="/usr/bin/pulumi"),
        patch("app.services.iac_destroy._run", side_effect=fake_run),
    ):
        result = run_workspace_iac_destroy(
            root_dir=str(tmp_path), engine="pulumi", credentials=None,
            org_id="o", workspace_id="w", settings=_settings(),
        )
    assert result.status == "skipped"


def test_result_ok_semantics() -> None:
    assert IaCDestroyResult("destroyed").ok is True
    assert IaCDestroyResult("skipped").ok is True
    assert IaCDestroyResult("failed").ok is False

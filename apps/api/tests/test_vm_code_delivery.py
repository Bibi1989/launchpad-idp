"""Regression tests for VM code delivery stability (sync vs clone)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.schemas.cloud import (
    InstanceCodeSource,
    InstanceProcessStrategy,
    InstanceReverseProxy,
    RunningInstanceConfig,
    RunningInstanceKind,
)
from app.services.attach_deploy import _prefer_workspace_sync
from app.services.git_urls import (
    is_launchpad_workspace_git_url,
    is_remote_cloneable_git_url,
)
from app.services.kubernetes import ProvisionedResources


def test_is_launchpad_workspace_git_url() -> None:
    assert is_launchpad_workspace_git_url(
        "https://launchpad.local/workspaces/579e90bb-2e03-4fa0-be62-7bd1013554c8/"
    )
    assert is_launchpad_workspace_git_url(
        "http://127.0.0.1/workspaces/abc"
    )
    assert not is_launchpad_workspace_git_url("https://github.com/acme/app.git")
    assert not is_launchpad_workspace_git_url("")


def test_is_remote_cloneable_git_url() -> None:
    assert is_remote_cloneable_git_url("https://github.com/acme/app.git")
    assert is_remote_cloneable_git_url("git@github.com:acme/app.git")
    assert not is_remote_cloneable_git_url(
        "https://launchpad.local/workspaces/abc/"
    )
    assert not is_remote_cloneable_git_url("")


def test_prefer_workspace_sync_for_placeholder_github_url(tmp_path: Path) -> None:
    assert _prefer_workspace_sync(
        code_source=InstanceCodeSource.GITHUB,
        git_repo_url=f"https://launchpad.local/workspaces/{tmp_path.name}",
        workspace_root=tmp_path,
    )
    assert not _prefer_workspace_sync(
        code_source=InstanceCodeSource.GITHUB,
        git_repo_url="https://github.com/acme/app.git",
        workspace_root=tmp_path,
    )
    # Linked remote wins over SSH code_source (clone on VM).
    assert not _prefer_workspace_sync(
        code_source=InstanceCodeSource.SSH,
        git_repo_url="https://github.com/acme/app.git",
        workspace_root=tmp_path,
    )
    # Import / scaffold with no remote: sync workspace disk.
    assert _prefer_workspace_sync(
        code_source=InstanceCodeSource.SSH,
        git_repo_url="https://launchpad.local/workspaces/abc/",
        workspace_root=tmp_path,
    )


def test_deploy_vm_native_syncs_when_git_url_is_launchpad_local(tmp_path: Path) -> None:
    from app.services import attach_deploy as ad

    synced: dict[str, object] = {}

    def fake_sync(**kwargs):  # type: ignore[no-untyped-def]
        synced.update(kwargs)

    monkey_patches = [
        patch.object(ad, "_wait_for_vm_ssh", return_value=None),
        patch.object(ad, "_wait_for_vm_host_ready", return_value=None),
        patch.object(ad, "_sync_workspace_over_ssh", side_effect=fake_sync),
        patch.object(ad, "_clone_repo_on_vm", side_effect=AssertionError("must not clone")),
        patch.object(ad, "_remote_shell", return_value=None),
        patch.object(ad, "_detect_start_command", return_value="node server.js"),
        patch.object(ad, "_resolve_app_workdir_rel", return_value="."),
    ]
    for p in monkey_patches:
        p.start()
    try:
        result = ad._deploy_vm_native(
            environment_id="95e19c3a-e7d4-4137-86d3-26b202854a48",
            name="demo",
            host="34.1.2.3",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                process_strategy=InstanceProcessStrategy.SYSTEMD,
                code_source=InstanceCodeSource.GITHUB,
                reverse_proxy=InstanceReverseProxy.NONE,
                listen_port=8080,
                ssh_user="ubuntu",
                ssh_port=22,
            ),
            settings=MagicMock(),
            cloud_provider="gcp",
            credentials=None,
            workspace_root=tmp_path,
            git_repo_url="https://launchpad.local/workspaces/579e90bb-2e03-4fa0-be62-7bd1013554c8/",
            git_branch="main",
            resources=ProvisionedResources(namespace="n", simulated=False),
            override="",
        )
    finally:
        for p in monkey_patches:
            p.stop()

    assert synced.get("workspace_root") == tmp_path
    assert result.preview_url == "http://34.1.2.3:8080"


def test_deploy_vm_native_clones_linked_github_url(tmp_path: Path) -> None:
    from app.services import attach_deploy as ad

    cloned: dict[str, object] = {}
    bootstraps: list[object] = []

    def fake_clone(**kwargs):  # type: ignore[no-untyped-def]
        cloned.update(kwargs)

    def fake_bootstrap(**kwargs):  # type: ignore[no-untyped-def]
        bootstraps.append(kwargs.get("autodetect_on_vm"))
        return "echo ok"

    monkey_patches = [
        patch.object(ad, "_wait_for_vm_ssh", return_value=None),
        patch.object(ad, "_wait_for_vm_host_ready", return_value=None),
        patch.object(
            ad, "_sync_workspace_over_ssh", side_effect=AssertionError("must not sync")
        ),
        patch.object(ad, "_clone_repo_on_vm", side_effect=fake_clone),
        patch.object(ad, "_native_bootstrap_and_start", side_effect=fake_bootstrap),
        patch.object(ad, "_remote_shell", return_value=None),
    ]
    for p in monkey_patches:
        p.start()
    try:
        result = ad._deploy_vm_native(
            environment_id="95e19c3a-e7d4-4137-86d3-26b202854a48",
            name="demo",
            host="34.1.2.3",
            running_instance=RunningInstanceConfig(
                kind=RunningInstanceKind.VM,
                process_strategy=InstanceProcessStrategy.SYSTEMD,
                code_source=InstanceCodeSource.SSH,
                reverse_proxy=InstanceReverseProxy.NONE,
                listen_port=8080,
                ssh_user="ubuntu",
                ssh_port=22,
            ),
            settings=MagicMock(),
            cloud_provider="gcp",
            credentials=None,
            workspace_root=tmp_path,
            git_repo_url="https://github.com/acme/app.git",
            git_branch="main",
            resources=ProvisionedResources(namespace="n", simulated=False),
            override="",
        )
    finally:
        for p in monkey_patches:
            p.stop()

    assert cloned.get("git_repo_url") == "https://github.com/acme/app.git"
    assert bootstraps == [True]
    assert result.preview_url == "http://34.1.2.3:8080"


def test_clone_repo_rejects_launchpad_local() -> None:
    from app.services.attach_deploy import AttachDeployError, _clone_repo_on_vm

    try:
        _clone_repo_on_vm(
            git_repo_url="https://launchpad.local/workspaces/abc",
            git_branch="main",
            app_dir="/opt/launchpad/x",
            running_instance=RunningInstanceConfig(kind=RunningInstanceKind.VM),
            host="1.2.3.4",
            cloud_provider="gcp",
            credentials=None,
            environment_id="eid",
        )
        raise AssertionError("expected AttachDeployError")
    except AttachDeployError as exc:
        assert "placeholder" in str(exc).lower() or "launchpad" in str(exc).lower()

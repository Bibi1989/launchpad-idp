"""AWS EC2 public-IP wait: poll instead of failing on the first empty read."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from app.services import cloud_instance_compute as cic


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["aws"], 0, stdout=stdout, stderr="")


def test_public_ip_treats_none_as_empty() -> None:
    with patch("app.services.aws_client.ec2_instance_public_ip", return_value=""):
        assert cic._aws_instance_public_ip(instance_id="i-1", region="us-east-1", env={}) == ""


def test_public_ip_returns_assigned_ip() -> None:
    with patch("app.services.aws_client.ec2_instance_public_ip", return_value="52.1.2.3"):
        assert cic._aws_instance_public_ip(instance_id="i-1", region="us-east-1", env={}) == "52.1.2.3"


def test_wait_polls_until_ip_appears() -> None:
    # First two reads: not-ready ("None" / empty); third: the IP is assigned.
    outputs = ["", "", "52.9.9.9"]
    with (
        patch("app.services.aws_client.ec2_instance_public_ip", side_effect=outputs),
        patch.object(cic.time, "sleep", return_value=None),
    ):
        host = cic._wait_aws_instance_ip(instance_id="i-1", region="us-east-1", env={}, attempts=5)
    assert host == "52.9.9.9"


def test_is_cloud_registry_image() -> None:
    from app.services.cloud_instance_compute import is_cloud_registry_image

    assert is_cloud_registry_image(
        "europe-west3-docker.pkg.dev/acme/launchpad-previews/e644:latest"
    )
    assert is_cloud_registry_image(
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-previews:app-latest"
    )
    assert not is_cloud_registry_image("nginx:1.27")
    assert not is_cloud_registry_image("launch-web:latest")


def test_teardown_cloud_registry_images_gcp() -> None:
    deleted: list[list[str]] = []

    def fake_delete(*, image: str, env: dict[str, str]) -> bool:
        deleted.append([image])
        return True

    with (
        patch.object(cic, "_delete_gcp_artifact_image", side_effect=fake_delete),
        patch.object(cic, "_credential_env", return_value={}),
    ):
        removed = cic.teardown_cloud_registry_images(
            [
                "europe-west3-docker.pkg.dev/acme-prod/launchpad-previews/e644e9a3eeee:latest"
            ],
            cloud_provider="gcp",
            credentials=None,
            region="europe-west3",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    assert removed
    assert any("e644e9a3eeee" in img for img in removed)


def test_wait_gives_up_after_attempts() -> None:
    with (
        patch.object(cic, "_run_cmd", return_value=_completed("None\n")),
        patch.object(cic.time, "sleep", return_value=None),
    ):
        host = cic._wait_aws_instance_ip(instance_id="i-1", region="us-east-1", env={}, attempts=3)
    assert host == ""


def test_build_and_push_cloud_image_uses_amd64_platform(tmp_path) -> None:
    from pathlib import Path

    app = tmp_path / "apps" / "web"
    app.mkdir(parents=True)
    (app / "Dockerfile").write_text("FROM node:20-alpine\n", encoding="utf-8")

    build_cmds: list[list[str]] = []

    def fake_run(cmd, *, timeout, check=True, env=None, input_text=None):
        build_cmds.append(cmd)
        if cmd[:2] == ["docker", "build"]:
            return _completed("")
        if cmd[:2] == ["docker", "tag"]:
            return _completed("")
        if cmd[:2] == ["docker", "push"]:
            return _completed("")
        return _completed("")

    with (
        patch.object(cic, "_run_cmd", side_effect=fake_run),
        patch.object(cic, "_credential_env", return_value={}),
        patch.object(cic, "resolve_gcp_project_id", return_value="acme-prod-123"),
        patch.object(cic, "_ensure_gcp_artifact_repo"),
        patch.object(cic, "_docker_auth_gcp"),
    ):
        remote = cic.build_and_push_cloud_image(
            workspace_root=Path(tmp_path),
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            cloud_provider="gcp",
            credentials=None,
            region="europe-west3",
        )

    assert build_cmds
    build = next(c for c in build_cmds if c[:2] == ["docker", "build"])
    assert "--platform" in build
    assert cic.CLOUD_CONTAINER_PLATFORM in build
    assert "--provenance=false" in build
    assert "--sbom=false" in build
    assert remote.startswith("europe-west3-docker.pkg.dev/acme-prod-123/launchpad-previews/")


def test_docker_auth_aws_logs_into_account_scoped_host() -> None:
    """Docker credentials must key off account.dkr.ecr.region, not region.dkr.ecr."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _completed("Login Succeeded\n")

    with (
        patch.object(cic, "_run_cmd", side_effect=fake_run),
        patch(
            "app.services.aws_client.ecr_login_password",
            return_value="ecr-password",
        ),
    ):
        cic._docker_auth_aws(
            region="eu-central-1",
            env={"PATH": "/usr/bin"},
            registry_host="851725202898.dkr.ecr.eu-central-1.amazonaws.com/launchpad-previews",
        )

    assert calls
    login = calls[0]
    assert login[:3] == ["docker", "login", "--username"]
    assert login[-1] == "851725202898.dkr.ecr.eu-central-1.amazonaws.com"
    assert "eu-central-1.dkr.ecr.amazonaws.com" not in login


def test_push_aws_uses_account_registry_for_login() -> None:
    login_hosts: list[str] = []

    def fake_auth(*, region: str, env: dict[str, str], registry_host: str) -> None:
        login_hosts.append(registry_host)

    with (
        patch.object(
            cic,
            "_ensure_aws_ecr_repo",
            return_value="851725202898.dkr.ecr.eu-central-1.amazonaws.com/launchpad-previews",
        ),
        patch.object(cic, "_docker_auth_aws", side_effect=fake_auth),
        patch.object(cic, "_credential_env", return_value={}),
        patch.object(cic, "_run_cmd", return_value=_completed("")),
        patch.object(cic, "pin_registry_image_to_platform", side_effect=lambda **kw: kw["image"]),
    ):
        remote = cic.push_local_image_to_cloud_registry(
            local_tag="launch-web:latest",
            image_name="launch-web",
            cloud_provider="aws",
            credentials=None,
            region="eu-central-1",
            environment_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

    assert login_hosts == ["851725202898.dkr.ecr.eu-central-1.amazonaws.com"]
    assert remote.startswith(
        "851725202898.dkr.ecr.eu-central-1.amazonaws.com/launchpad-previews:launch-web-"
    )

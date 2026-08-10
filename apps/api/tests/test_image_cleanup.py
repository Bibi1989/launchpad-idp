"""Tests for Docker image cleanup on preview/workspace destroy."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services.image_cleanup import (
    collect_preview_environment_images,
    collect_workspace_destroy_images,
    is_removable_app_image,
    remove_local_docker_images,
)
from app.services.manifest_deploy import collect_workspace_image_tags


def test_is_removable_skips_shared_base_images() -> None:
    assert is_removable_app_image("launch-web:latest")
    assert is_removable_app_image("launchpad-preview/abc:deadbeef")
    assert not is_removable_app_image("nginx:1.27-alpine")
    assert not is_removable_app_image("redis:7")
    assert not is_removable_app_image("app:latest", default_image="app:latest")


def test_collect_workspace_image_tags_from_plan(tmp_path: Path) -> None:
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM nginx:alpine\n", encoding="utf-8")
    plan_dir = tmp_path / ".launchpad"
    plan_dir.mkdir()
    (plan_dir / "image-builds.json").write_text(
        json.dumps(
            [
                {
                    "service": "launch-web",
                    "image": "launch-web:latest",
                    "context": "apps/web",
                    "dockerfile": "apps/web/Dockerfile",
                }
            ]
        ),
        encoding="utf-8",
    )

    tags = collect_workspace_image_tags(tmp_path)
    assert "launch-web:latest" in tags


def test_collect_workspace_destroy_images_merges_workload(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "api"
    app.mkdir(parents=True)
    (app / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    tags = collect_workspace_destroy_images(
        tmp_path,
        workload_images=["custom-preview:sha1", "nginx:1.27-alpine"],
    )
    assert "api:latest" in tags or "launch-api:latest" in tags
    assert "custom-preview:sha1" in tags
    assert "nginx:1.27-alpine" in tags  # filtered later at remove time


def test_collect_preview_environment_images_includes_prefix_tags() -> None:
    env_id = str(uuid4())
    settings = Settings(
        _env_file=None,
        preview_build_image_prefix="launchpad-preview",
        preview_image_registry=None,
    )
    slug = env_id.replace("-", "")[:12]
    expected_repo = f"launchpad-preview/{slug}"

    with patch(
        "app.services.image_cleanup.list_host_images_for_reference",
        return_value=[f"{expected_repo}:aaa", f"{expected_repo}:bbb"],
    ):
        images = collect_preview_environment_images(
            settings=settings,
            environment_id=env_id,
            workload_image=f"{expected_repo}:aaa",
            commit_sha="abcdef1234567890",
        )

    assert f"{expected_repo}:aaa" in images
    assert f"{expected_repo}:bbb" in images
    assert any(img.startswith(f"{expected_repo}:") for img in images)


def test_collect_preview_skips_shared_workspace_tags() -> None:
    env_id = str(uuid4())
    settings = Settings(
        _env_file=None,
        preview_build_image_prefix="launchpad-preview",
        preview_image_registry=None,
    )
    with patch(
        "app.services.image_cleanup.list_host_images_for_reference",
        return_value=[],
    ):
        images = collect_preview_environment_images(
            settings=settings,
            environment_id=env_id,
            workload_image="launch-web:latest",
            commit_sha=None,
        )
    assert images == []


def test_remove_local_docker_images_skips_denylist_and_calls_rmi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr("app.services.image_cleanup.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr("app.services.image_cleanup.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.services.image_cleanup.get_settings",
        lambda: Settings(
            _env_file=None,
            default_workload_image="nginx:1.27-alpine",
            local_k8s_engine="k3s",
        ),
    )

    removed = remove_local_docker_images(
        ["launch-web:latest", "nginx:1.27-alpine", "redis:7"],
        cluster_name="launchpad",
        remove_from_cluster=True,
    )

    assert removed == ["launch-web:latest"]
    rmi_cmds = [c for c in calls if c[:3] == ["docker", "rmi", "-f"]]
    assert rmi_cmds == [["docker", "rmi", "-f", "launch-web:latest"]]
    crictl = [c for c in calls if "crictl" in c]
    assert any("launch-web:latest" in c for c in crictl)

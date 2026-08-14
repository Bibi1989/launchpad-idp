"""Tests for Dockerfile → local cluster image build planning."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.manifest_deploy import (
    BUILD_FINGERPRINT_LABEL,
    build_and_load_kind_images,
    cluster_has_image,
    plan_workspace_image_builds,
    workspace_image_fingerprint,
    _load_image_to_local_cluster,
)


def test_plan_skips_heuristic_aliases_when_image_builds_json_exists(tmp_path: Path) -> None:
    web = tmp_path / "apps" / "nestjs"
    web.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM node:20-alpine\n", encoding="utf-8")
    plan_dir = tmp_path / ".launchpad"
    plan_dir.mkdir()
    (plan_dir / "image-builds.json").write_text(
        json.dumps(
            [
                {
                    "service": "launch-nestjs",
                    "image": "launch-nestjs:latest",
                    "context": "apps/nestjs",
                    "dockerfile": "apps/nestjs/Dockerfile",
                }
            ]
        ),
        encoding="utf-8",
    )

    builds, required = plan_workspace_image_builds(tmp_path)
    tags = [tag for _, _, tag in builds]
    assert tags == ["launch-nestjs:latest"]
    assert required == {"launch-nestjs:latest"}
    assert "nestjs:latest" not in tags
    assert "launchpad/nestjs:latest" not in tags


def test_plan_adds_uncovered_apps_when_image_builds_incomplete(tmp_path: Path) -> None:
    """Stale root-only plans must not hide apps/api-server Deployments."""
    (tmp_path / "Dockerfile").write_text("FROM alpine:3.21\n", encoding="utf-8")
    api = tmp_path / "apps" / "api-server"
    api.mkdir(parents=True)
    (api / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    web = tmp_path / "apps" / "web-ui"
    web.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM node:20-alpine\n", encoding="utf-8")
    plan_dir = tmp_path / ".launchpad"
    plan_dir.mkdir()
    (plan_dir / "image-builds.json").write_text(
        json.dumps(
            [
                {
                    "service": "launch-app",
                    "image": "launch-app:latest",
                    "context": ".",
                    "dockerfile": "Dockerfile",
                }
            ]
        ),
        encoding="utf-8",
    )

    builds, required = plan_workspace_image_builds(tmp_path)
    tags = {tag for _, _, tag in builds}
    assert "launch-app:latest" in tags
    assert "api-server:latest" in tags
    assert "web-ui:latest" in tags
    assert "api-server:latest" in required
    assert "web-ui:latest" in required
    assert "launchpad/api-server:latest" not in tags


def test_plan_adds_launch_web_alias_when_plan_uses_short_tag(tmp_path: Path) -> None:
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM nginx:alpine\n", encoding="utf-8")
    plan_dir = tmp_path / ".launchpad"
    plan_dir.mkdir()
    (plan_dir / "image-builds.json").write_text(
        json.dumps(
            [
                {
                    "service": "web",
                    "image": "web:latest",
                    "context": "apps/web",
                    "dockerfile": "apps/web/Dockerfile",
                }
            ]
        ),
        encoding="utf-8",
    )

    builds, required = plan_workspace_image_builds(tmp_path)
    tags = {tag for _, _, tag in builds}
    assert "web:latest" in tags
    assert "launch-web:latest" in tags
    assert required == {"web:latest"}


def test_build_and_load_imports_all_alias_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "Dockerfile").write_text("FROM nginx:alpine\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = "sha256:deadbeef"
            stderr = ""

        return _R()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    with (
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_local_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._host_image_fingerprint", return_value=None),
        patch("app.services.manifest_deploy._load_image_to_local_cluster", return_value=True) as load_mock,
    ):
        loaded = build_and_load_kind_images(tmp_path, cluster_name="launchpad")

    loaded_tags = set(loaded)
    assert "web:latest" in loaded_tags
    assert "launch-web:latest" in loaded_tags
    assert load_mock.call_count >= 2


def test_build_and_load_uses_image_builds_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = "sha256:abc123"
            stderr = ""

        return _R()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    with (
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_local_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._host_image_fingerprint", return_value=None),
        patch("app.services.manifest_deploy._load_image_to_local_cluster", return_value=True),
    ):
        loaded = build_and_load_kind_images(tmp_path, cluster_name="k3d-launchpad")

    assert "launch-web:latest" in loaded
    build_cmds = [c for c in calls if c[:2] == ["docker", "build"]]
    assert len(build_cmds) == 1
    assert any("launch-web:latest" in c for c in build_cmds)
    assert any(BUILD_FINGERPRINT_LABEL in part for c in build_cmds for part in c)


def test_build_and_load_dedupes_dockerfile_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = tmp_path / "apps" / "api"
    api.mkdir(parents=True)
    (api / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = "sha256:deadbeef"
            stderr = ""

        return _R()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    with (
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_local_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._host_image_fingerprint", return_value=None),
        patch("app.services.manifest_deploy._load_image_to_local_cluster", return_value=True) as load_mock,
    ):
        loaded = build_and_load_kind_images(tmp_path, cluster_name="launchpad")

    build_cmds = [c for c in calls if c[:2] == ["docker", "build"]]
    tag_cmds = [c for c in calls if c[:2] == ["docker", "tag"]]
    assert len(build_cmds) == 1
    assert len(tag_cmds) >= 1
    assert load_mock.call_count >= 1
    assert any(
        t.startswith("api:") or t.startswith("launch-api:") or t.startswith("launchpad/api:")
        for t in loaded
    )


def test_build_skips_when_fingerprint_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    df = web / "Dockerfile"
    df.write_text("FROM nginx:alpine\n", encoding="utf-8")
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
    fingerprint = workspace_image_fingerprint(dockerfile=df, context=web)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = "sha256:cached"
            stderr = ""

        return _R()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    with (
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_local_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._host_image_fingerprint", return_value=fingerprint),
        patch("app.services.manifest_deploy._host_docker_image_id", return_value="sha256:cached"),
        patch("app.services.manifest_deploy._load_image_to_local_cluster", return_value=True) as load_mock,
    ):
        loaded = build_and_load_kind_images(tmp_path, cluster_name="launchpad")

    assert "launch-web:latest" in loaded
    assert not any(c[:2] == ["docker", "build"] for c in calls)
    assert load_mock.call_count == 1


def test_cluster_has_image_matches_tag_and_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = "docker.io/library/launch-web:latest sha256:abcdef0123456789"

    def fake_run(cmd, **kwargs):
        class _R:
            returncode = 0
            stdout = listing
            stderr = ""

        return _R()

    monkeypatch.setattr(
        "app.services.manifest_deploy._local_node_container_names",
        lambda *_a, **_k: ["k3d-launchpad-server-0"],
    )
    with (
        patch("app.services.manifest_deploy.resolve_kind_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._host_docker_image_id", return_value="sha256:abcdef0123456789"),
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
    ):
        assert cluster_has_image("launch-web:latest", cluster_name="launchpad", engine="k3s") is True


def test_load_image_skips_import_when_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    with (
        patch("app.services.manifest_deploy.cluster_has_image", return_value=True),
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_kind_cluster_name", return_value="launchpad"),
    ):
        assert _load_image_to_local_cluster("launch-web:latest", cluster_name="launchpad") is True

    assert not any("import" in c or "load" in c for c in calls)

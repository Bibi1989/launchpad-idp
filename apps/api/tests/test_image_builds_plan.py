"""Tests for Dockerfile → local cluster image build planning."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.manifest_deploy import build_and_load_kind_images


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
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    with (
        patch("app.services.manifest_deploy.subprocess.run", side_effect=fake_run),
        patch("app.services.manifest_deploy.resolve_local_cluster_name", return_value="launchpad"),
        patch("app.services.manifest_deploy._load_image_to_local_cluster", return_value=True),
    ):
        loaded = build_and_load_kind_images(tmp_path, cluster_name="k3d-launchpad")

    assert "launch-web:latest" in loaded
    build_cmds = [c for c in calls if c[:2] == ["docker", "build"]]
    assert any("launch-web:latest" in c for c in build_cmds)

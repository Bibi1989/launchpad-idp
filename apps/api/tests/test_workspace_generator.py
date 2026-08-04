"""Tests for WorkspaceGenerator manifest + Dockerfile scaffolding."""

from __future__ import annotations

from pathlib import Path

from pkg.detector.models import (
    DetectedService,
    DetectionResult,
    MonorepoTool,
    ProjectLayout,
    ServiceRole,
)
from pkg.generator.workspace import WorkspaceGenerator


def test_workspace_generator_writes_launch_manifests(tmp_path: Path) -> None:
    detection = DetectionResult(
        layout=ProjectLayout.MONOREPO,
        monorepo_tools=[MonorepoTool.PNPM],
        services=[
            DetectedService(
                id="web",
                name="launch-web",
                path="apps/web",
                role=ServiceRole.WEB,
                framework="nextjs",
                runtime="node",
                port=3000,
                is_preview_target=True,
            ),
            DetectedService(
                id="api",
                name="launch-server",
                path="apps/api",
                role=ServiceRole.API,
                framework="express",
                runtime="node",
                port=3001,
            ),
        ],
        datastores=["postgres"],
    )
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "api").mkdir(parents=True)

    generated = WorkspaceGenerator().generate(
        tmp_path,
        detection,
        workspace_name="demo-import",
    )
    assert generated.preview_service == "launch-web"
    assert any("launch-web-deployment.yaml" in f for f in generated.manifests)
    assert any("launch-server-service.yaml" in f for f in generated.manifests)
    assert (tmp_path / "infra" / "k8s" / "manifests" / "ingress.yaml").is_file()
    assert (tmp_path / "IMPORT.md").is_file()
    # Missing Dockerfiles get scaffolded
    assert (tmp_path / "apps" / "web" / "Dockerfile").is_file()
    assert (tmp_path / "apps" / "api" / "Dockerfile").is_file()
    plan = (tmp_path / ".launchpad" / "image-builds.json").read_text(encoding="utf-8")
    assert "launch-web:latest" in plan
    assert "launch-server:latest" in plan
    assert "apps/web/Dockerfile" in plan

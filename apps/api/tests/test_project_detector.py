"""Unit tests for ProjectDetectorEngine monorepo / single-project detection."""

from __future__ import annotations

from pathlib import Path

from pkg.detector import ProjectDetectorEngine, ProjectLayout, ServiceRole


def test_detect_single_nextjs_project(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"web","dependencies":{"next":"14.0.0","react":"18.0.0"}}',
        encoding="utf-8",
    )
    result = ProjectDetectorEngine().detect(tmp_path)
    assert result.layout == ProjectLayout.SINGLE
    assert len(result.services) == 1
    svc = result.services[0]
    assert svc.framework == "nextjs"
    assert svc.role == ServiceRole.WEB
    assert svc.port == 3000
    assert svc.is_preview_target is True


def test_detect_pnpm_monorepo_web_and_api(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/*'\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"name":"root","private":true}', encoding="utf-8")
    apps = tmp_path / "apps"
    web = apps / "web"
    api = apps / "api"
    web.mkdir(parents=True)
    api.mkdir(parents=True)
    (web / "package.json").write_text(
        '{"name":"web","dependencies":{"next":"14.0.0"}}',
        encoding="utf-8",
    )
    (api / "package.json").write_text(
        '{"name":"api","dependencies":{"express":"4.18.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n  cache:\n    image: redis:7\n",
        encoding="utf-8",
    )

    result = ProjectDetectorEngine().detect(tmp_path)
    assert result.layout == ProjectLayout.MONOREPO
    assert "pnpm" in [t.value for t in result.monorepo_tools]
    assert "postgres" in result.datastores
    assert "redis" in result.datastores
    names = {s.name for s in result.services}
    assert "launch-web" in names
    assert "launch-server" in names
    preview = next(s for s in result.services if s.is_preview_target)
    assert preview.role == ServiceRole.WEB
    assert result.has_compose is True


def test_detect_fastapi_single(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="api"\ndependencies=["fastapi","uvicorn"]\n',
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    result = ProjectDetectorEngine().detect(tmp_path)
    assert result.layout == ProjectLayout.SINGLE
    svc = result.services[0]
    assert svc.framework == "fastapi"
    assert svc.port == 8000
    assert svc.has_dockerfile is True


def test_detect_runtime_hints_compose_and_kubernetes(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"web","dependencies":{"next":"14.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: node:22\n",
        encoding="utf-8",
    )
    manifests = tmp_path / "k8s"
    manifests.mkdir()
    (manifests / "deploy.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n",
        encoding="utf-8",
    )
    result = ProjectDetectorEngine().detect(tmp_path)
    assert result.has_compose is True
    assert result.has_kubernetes is True
    assert "docker compose found" in result.summary
    assert "kubernetes manifests found" in result.summary

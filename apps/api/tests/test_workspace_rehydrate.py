"""Tests for hollow workspace detection and durable IaC root."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.manifest_deploy import (
    ensure_workspace_k8s_manifests,
    workspace_has_application_source,
    workspace_is_nginx_scaffold_only,
)


def test_iac_workspace_root_rejects_tmp(monkeypatch) -> None:
    monkeypatch.setenv("IAC_WORKSPACE_ROOT", "/tmp/launchpad-workspaces")
    settings = Settings(_env_file=None)
    resolved = Path(settings.iac_workspace_root).expanduser().resolve()
    assert Path("/tmp").resolve() not in resolved.parents
    assert resolved != Path("/tmp").resolve()
    assert resolved.name == "workspaces"


def test_nginx_scaffold_only_detection(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "infra" / "k8s" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "deployment.yaml").write_text(
        """apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: nginx:1.27-alpine
""",
        encoding="utf-8",
    )
    (manifest_dir / "service.yaml").write_text(
        """apiVersion: v1
kind: Service
metadata:
  name: app
spec:
  selector:
    app: app
  ports:
    - port: 80
""",
        encoding="utf-8",
    )
    assert not workspace_has_application_source(tmp_path)
    assert workspace_is_nginx_scaffold_only(tmp_path)


def test_ensure_refuses_empty_workspace(tmp_path: Path) -> None:
    try:
        ensure_workspace_k8s_manifests(tmp_path, image="nginx:1.27-alpine")
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised


def test_source_marker_detected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert workspace_has_application_source(tmp_path)
    assert not workspace_is_nginx_scaffold_only(tmp_path)

"""Tests for workspace file CRUD helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.workspace_files import (
    WorkspaceFileError,
    delete_path,
    format_content,
    list_file_tree,
    mkdir,
    read_file,
    rename_path,
    write_file,
)


def test_workspace_file_crud_roundtrip(tmp_path: Path) -> None:
    write_file(tmp_path, "infra/k8s/manifests/pod.yaml", "apiVersion: v1\n")
    mkdir(tmp_path, "infra/terraform/modules")
    nodes = list_file_tree(tmp_path)
    paths = {n["path"] for n in nodes}
    assert "infra/k8s/manifests/pod.yaml" in paths
    assert "infra/terraform/modules" in paths
    assert read_file(tmp_path, "infra/k8s/manifests/pod.yaml").startswith("apiVersion")

    rename_path(tmp_path, "infra/k8s/manifests/pod.yaml", "infra/k8s/manifests/app-pod.yaml")
    assert "apiVersion" in read_file(tmp_path, "infra/k8s/manifests/app-pod.yaml")
    delete_path(tmp_path, "infra/k8s/manifests/app-pod.yaml")
    with pytest.raises(WorkspaceFileError):
        read_file(tmp_path, "infra/k8s/manifests/app-pod.yaml")


def test_workspace_path_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceFileError):
        write_file(tmp_path, "../escape.txt", "nope")
    with pytest.raises(WorkspaceFileError):
        read_file(tmp_path, "../../etc/passwd")


def test_format_yaml_and_json() -> None:
    yaml_out = format_content("x.yaml", "kind: Pod\napiVersion: v1\nmetadata: {name: app}\n")
    assert "kind: Pod" in yaml_out
    json_out = format_content("x.json", '{"a":1,"b":2}')
    assert '"a": 1' in json_out


def test_hidden_paths_excluded(tmp_path: Path) -> None:
    (tmp_path / ".launchpad").mkdir()
    (tmp_path / ".launchpad" / "secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    write_file(tmp_path, "visible.txt", "ok")
    write_file(tmp_path, ".gitignore", "*.log\n")
    write_file(
        tmp_path,
        "ci/github/workflows/deploy.yml",
        "name: Deploy\n",
    )
    paths = {n["path"] for n in list_file_tree(tmp_path)}
    assert "visible.txt" in paths
    assert ".gitignore" in paths
    assert "ci/github/workflows/deploy.yml" in paths
    assert not any(p.startswith(".launchpad") for p in paths)
    assert not any(p.startswith(".git/") or p == ".git" for p in paths)
    assert read_file(tmp_path, ".gitignore").startswith("*")
    assert "name: Deploy" in read_file(
        tmp_path,
        "ci/github/workflows/deploy.yml",
    )
    with pytest.raises(WorkspaceFileError, match="Hidden paths are not accessible"):
        read_file(tmp_path, ".launchpad/secret.json")

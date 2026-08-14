"""OCI image layout must not IndexError when resolving Settings env files."""

from __future__ import annotations

from pathlib import Path

from app.core.config import resolve_settings_paths


def test_resolve_settings_paths_oci_layout() -> None:
    api_dir, repo_root = resolve_settings_paths(Path("/app/app/core/config.py"))
    assert api_dir == Path("/app")
    assert repo_root == Path("/app")


def test_resolve_settings_paths_monorepo(tmp_path: Path) -> None:
    repo = tmp_path / "launchpad"
    (repo / "apps").mkdir(parents=True)
    (repo / "deploy").mkdir()
    config = repo / "apps" / "api" / "app" / "core" / "config.py"
    config.parent.mkdir(parents=True)
    config.write_text("# stub\n", encoding="utf-8")
    api_dir, repo_root = resolve_settings_paths(config)
    assert api_dir == repo / "apps" / "api"
    assert repo_root == repo.resolve()

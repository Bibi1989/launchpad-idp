"""OCI image layout must not IndexError when resolving Settings env files."""

from __future__ import annotations

from pathlib import Path

from app.core.config import _API_DIR, _ENV_FILES, _REPO_ROOT, resolve_settings_paths


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


def test_env_files_only_api_dotenv() -> None:
    """API must load only apps/api/.env (not repo-root or deploy/oci)."""
    resolved = {str(Path(p).resolve()) for p in _ENV_FILES}
    assert resolved == {str((_API_DIR / ".env").resolve())}
    assert str((_REPO_ROOT / ".env").resolve()) not in resolved
    assert str((_REPO_ROOT / "deploy" / "oci" / ".env").resolve()) not in resolved

"""Tests for GitImporterService path isolation and URL validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services.git_importer import GitImporterError, GitImporterService


def test_git_importer_rejects_bad_url(tmp_path: Path) -> None:
    settings = Settings(repo_import_root=str(tmp_path / "imports"), _env_file=None)
    svc = GitImporterService(settings)
    with pytest.raises(GitImporterError, match="http"):
        svc.clone(repo_url="not-a-url", branch="main")


def test_git_importer_clone_writes_meta(tmp_path: Path) -> None:
    settings = Settings(repo_import_root=str(tmp_path / "imports"), _env_file=None)
    svc = GitImporterService(settings)

    def fake_clone(**kwargs):
        dest: Path = kwargs["dest"]
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("ok\n", encoding="utf-8")
        return "abc123deadbeef"

    with patch("app.services.git_importer.clone_git_repository", side_effect=fake_clone):
        result = svc.clone(
            repo_url="https://github.com/acme/demo.git",
            branch="main",
            import_id="11111111-1111-1111-1111-111111111111",
        )

    assert result.commit_sha.startswith("abc123")
    assert result.root_dir.is_dir()
    meta = svc.read_meta(result.import_id)
    assert meta["repo_url"] == "https://github.com/acme/demo.git"
    assert svc.cleanup(result.import_id) is True

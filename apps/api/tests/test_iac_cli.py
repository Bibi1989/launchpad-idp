"""Tests for Pulumi / Terraform CLI resolution and auto-install helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.iac_cli import (
    IaCCliError,
    pulumi_was_applied,
    resolve_pulumi_bin,
    tools_bin_dir,
)


def test_pulumi_was_applied_requires_local_state(tmp_path: Path) -> None:
    pulumi_dir = tmp_path / "infra" / "pulumi"
    pulumi_dir.mkdir(parents=True)
    (pulumi_dir / "Pulumi.yaml").write_text("name: demo\n", encoding="utf-8")
    assert pulumi_was_applied(pulumi_dir) is False
    (pulumi_dir / ".pulumi").mkdir()
    assert pulumi_was_applied(pulumi_dir) is True


def test_resolve_pulumi_bin_uses_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "pulumi"
    fake.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_pulumi_bin(install_if_missing=False) == str(fake.resolve())


def test_resolve_pulumi_bin_missing_without_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    with (
        patch("app.services.iac_cli.shutil.which", return_value=None),
        patch("app.services.iac_cli.tools_bin_dir", return_value=Path("/tmp/launchpad-no-tools")),
        pytest.raises(IaCCliError, match="not installed"),
    ):
        resolve_pulumi_bin(install_if_missing=False)


def test_tools_bin_dir_under_launchpad(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IAC_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        path = tools_bin_dir()
        assert path == (tmp_path / "tools" / "bin")
        assert path.is_dir()
    finally:
        get_settings.cache_clear()

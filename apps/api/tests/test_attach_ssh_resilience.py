"""SSH keepalive + transient retry helpers for cloud VM configure."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from app.services.ansible_runner import run_ansible_site
from app.services.attach_deploy import (
    AttachDeployError,
    _is_transient_ssh_error,
    _run_with_ssh_retries,
    _ssh_client_option_args,
)


def test_ssh_client_options_include_keepalive() -> None:
    opts = _ssh_client_option_args()
    joined = " ".join(opts)
    assert "ServerAliveInterval=30" in joined
    assert "ServerAliveCountMax=10" in joined
    assert "BatchMode=yes" in joined


def test_transient_ssh_error_detection() -> None:
    assert _is_transient_ssh_error(
        AttachDeployError("Connection reset by peer")
    )
    assert _is_transient_ssh_error(
        AttachDeployError("Connection to 1.2.3.4 closed by remote host.")
    )
    assert not _is_transient_ssh_error(AttachDeployError("Permission denied"))


def test_run_with_ssh_retries_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_run(cmd, **_):
        calls["n"] += 1
        if calls["n"] < 3:
            raise AttachDeployError("ssh… failed: Connection reset by peer")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("app.services.attach_deploy._run", fake_run)
    with patch("time.sleep", lambda *_: None):
        result = _run_with_ssh_retries(["ssh", "host", "echo"], timeout=10, attempts=5)
    assert result.returncode == 0
    assert calls["n"] == 3


def test_ansible_timeout_returns_failed(tmp_path) -> None:
    ansible = tmp_path / "infra" / "ansible"
    (ansible / "playbooks").mkdir(parents=True)
    (ansible / "playbooks" / "site.yml").write_text("---\n", encoding="utf-8")
    fake_bin = tmp_path / "ansible-playbook"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    with (
        patch(
            "app.services.ansible_runner._resolve_ansible_playbook",
            return_value=str(fake_bin),
        ),
        patch(
            "app.services.ansible_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ansible-playbook"], timeout=1),
        ),
    ):
        result = run_ansible_site(tmp_path, timeout_seconds=1)
    assert result.status == "failed"
    assert "timed out" in result.detail

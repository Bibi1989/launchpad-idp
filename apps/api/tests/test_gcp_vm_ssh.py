"""Tests for GCP VM SSH metadata helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.gcp_vm_ssh import (
    ensure_gcp_instance_ssh_metadata,
    patch_workspace_disable_os_login,
)


def test_patch_workspace_disable_os_login_pulumi(tmp_path: Path) -> None:
    pulumi = tmp_path / "infra" / "pulumi"
    pulumi.mkdir(parents=True)
    target = pulumi / "index.ts"
    target.write_text(
        'metadata: { "enable-oslogin": "TRUE", "ssh-keys": key },\n',
        encoding="utf-8",
    )
    assert patch_workspace_disable_os_login(tmp_path) == 1
    text = target.read_text(encoding="utf-8")
    assert '"FALSE"' in text
    assert "TRUE" not in text


def test_ensure_gcp_instance_ssh_metadata_invokes_gcloud() -> None:
    calls: list[list[str]] = []
    written: list[str] = []

    def fake_run(cmd, **_):
        calls.append(list(cmd))
        # Capture keys file content before caller deletes it.
        for arg in cmd:
            if arg.startswith("--metadata-from-file=ssh-keys="):
                path = arg.removeprefix("--metadata-from-file=ssh-keys=")
                written.append(Path(path).read_text(encoding="utf-8"))

        class R:
            returncode = 0
            stdout = "Updated"
            stderr = ""

        return R()

    with (
        patch("app.services.gcp_vm_ssh.shutil.which", return_value="/usr/bin/gcloud"),
        patch("app.services.gcp_vm_ssh.subprocess.run", side_effect=fake_run),
        patch("app.services.gcp_vm_ssh.time.sleep", return_value=None),
        patch(
            "app.services.gcp_vm_ssh._credential_env",
            return_value={"GOOGLE_CLOUD_PROJECT": "demo"},
        ),
    ):
        ensure_gcp_instance_ssh_metadata(
            instance_name="lp-demo-vm",
            zone="europe-west3",
            public_key_line="ssh-ed25519 AAAA launchpad-preview",
            environment_id="env-1",
            credentials=None,
            project_id="demo",
        )

    assert calls
    cmd = calls[0]
    assert cmd[:5] == [
        "gcloud",
        "compute",
        "instances",
        "add-metadata",
        "lp-demo-vm",
    ]
    assert "--zone=europe-west3-a" in cmd
    assert "--metadata=enable-oslogin=FALSE" in cmd
    assert sum(1 for a in cmd if a.startswith("--metadata=")) == 1
    assert any(a.startswith("--metadata-from-file=ssh-keys=") for a in cmd)
    assert written == ["ubuntu:ssh-ed25519 AAAA launchpad-preview\n"]


def test_ensure_gcp_instance_ssh_metadata_requires_gcloud() -> None:
    with patch("app.services.gcp_vm_ssh.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="gcloud"):
            ensure_gcp_instance_ssh_metadata(
                instance_name="lp-demo-vm",
                zone="europe-west3-a",
                public_key_line="ssh-ed25519 AAAA launchpad-preview",
                environment_id="env-1",
                credentials=None,
            )

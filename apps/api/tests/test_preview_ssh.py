"""Tests for preview SSH key helpers."""

from __future__ import annotations

from app.services.preview_ssh import (
    authorized_keys_user_data_snippet,
    ensure_preview_ssh_keypair,
)


def test_ensure_preview_ssh_keypair_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IAC_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    env_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    path1, pub1 = ensure_preview_ssh_keypair(env_id)
    path2, pub2 = ensure_preview_ssh_keypair(env_id)
    assert path1 == path2
    assert pub1 == pub2
    assert pub1.startswith("ssh-ed25519 ")


def test_authorized_keys_user_data_snippet() -> None:
    snippet = authorized_keys_user_data_snippet(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI launchpad-preview",
        user="ec2-user",
    )
    assert "/home/ec2-user/.ssh/authorized_keys" in snippet
    assert "chown -R ec2-user:ec2-user" in snippet

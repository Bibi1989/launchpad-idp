"""Configuration runner: wraps the Ansible CLI against an existing ``playbook.yml``.

This plugin does NOT author any Ansible - it runs the playbook you point it at against a
target host IP using an ad-hoc inventory (``-i "<host>,"``). The ``ansible-playbook``
binary is resolved via the existing ``ansible_runner._resolve_ansible_playbook`` helper so
tool bootstrapping matches the rest of Launchpad.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.services.ansible_runner import _resolve_ansible_playbook

from .base import CloudServicePlugin, PluginResult, PluginStatus

logger = get_logger(__name__)

_ANSIBLE_ENV = {
    "ANSIBLE_HOST_KEY_CHECKING": "False",
    "ANSIBLE_RETRY_FILES_ENABLED": "False",
    "ANSIBLE_SSH_ARGS": (
        "-o BatchMode=yes -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o ConnectTimeout=20"
    ),
}


class AnsibleConfigPlugin(CloudServicePlugin):
    """Runs the existing ``playbook.yml`` against a host IP passed in ``inputs['host']``."""

    id = "ansible"

    def __init__(
        self,
        playbook_path: str | Path,
        *,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        private_key_path: str | None = None,
        timeout_seconds: float = 900.0,
        base_env: dict[str, str] | None = None,
    ) -> None:
        self.playbook_path = Path(playbook_path)
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.private_key_path = private_key_path
        self.timeout_seconds = timeout_seconds
        # See AwsTerraformPlugin.base_env - restricts the env for untrusted user IaC.
        self.base_env = base_env

    # --- lifecycle ---
    def provision(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        host = str((inputs or {}).get("host") or "").strip()
        if not host:
            return PluginResult(PluginStatus.FAILED, "AnsibleConfigPlugin requires inputs['host']")
        binary = _resolve_ansible_playbook()
        if not binary or not Path(binary).exists():
            return PluginResult(PluginStatus.SKIPPED, "ansible-playbook CLI not available")
        if not self.playbook_path.is_file():
            return PluginResult(PluginStatus.SKIPPED, f"playbook not found: {self.playbook_path}")

        ssh_user = str((inputs or {}).get("ssh_user") or self.ssh_user)
        cmd = [
            binary,
            str(self.playbook_path),
            "-i",
            f"{host},",  # trailing comma => ad-hoc inventory with a single host
            "-u",
            ssh_user,
            "--ssh-common-args",
            f"-o Port={self.ssh_port}",
        ]
        if self.private_key_path:
            cmd += ["--private-key", self.private_key_path]
        proc = self._run(binary, cmd)
        status = PluginStatus.SUCCESS if proc.returncode == 0 else PluginStatus.FAILED
        return PluginResult(status, f"ansible-playbook rc={proc.returncode}", raw=_combined(proc))

    def destroy(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        # Ansible is configuration management, not lifecycle management: there is no
        # generic "destroy". Provide a teardown playbook and run provision() against it
        # instead if you need one.
        return PluginResult(PluginStatus.SKIPPED, "ansible has no destroy action")

    def get_status(self, inputs: Mapping[str, Any] | None = None) -> PluginResult:
        host = str((inputs or {}).get("host") or "").strip()
        if not host:
            return PluginResult(PluginStatus.UNKNOWN, "no host to check")
        playbook_bin = _resolve_ansible_playbook()
        # `ansible` (ad-hoc) lives next to `ansible-playbook`.
        ansible_bin = str(Path(playbook_bin).with_name("ansible")) if playbook_bin else None
        if not ansible_bin or not Path(ansible_bin).exists():
            return PluginResult(PluginStatus.UNKNOWN, "ansible CLI not available for status check")
        cmd = [ansible_bin, "all", "-i", f"{host},", "-u",
               str((inputs or {}).get("ssh_user") or self.ssh_user),
               "--ssh-common-args", f"-o Port={self.ssh_port}", "-m", "ping"]
        if self.private_key_path:
            cmd += ["--private-key", self.private_key_path]
        proc = self._run(ansible_bin, cmd)
        reachable = proc.returncode == 0
        return PluginResult(
            PluginStatus.RUNNING if reachable else PluginStatus.UNKNOWN,
            "host reachable" if reachable else "host unreachable",
            raw=_combined(proc),
        )

    # --- internals: wrap the Ansible CLI ---
    def _run(self, binary: str, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        base = self.base_env if self.base_env is not None else dict(os.environ)
        env = {**base, **_ANSIBLE_ENV}
        # Make the sibling ansible-galaxy/ansible tools visible.
        env["PATH"] = f"{Path(binary).parent}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            cmd,
            cwd=str(self.playbook_path.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    return ((proc.stdout or "") + (proc.stderr or ""))[-8000:]

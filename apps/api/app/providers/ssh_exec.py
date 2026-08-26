"""Minimal SSH exec helper built on the system ``ssh`` client.

Used by VM providers for optional post-boot operations (running the health-poll script,
tailing logs, restarting the unit). Cloud-init handles first-boot bootstrap; this is only
for follow-up commands. No paramiko dependency - matches the existing codebase, which
already shells out to ``ssh`` for VM deploys.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SSHResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _base_opts(*, port: int, connect_timeout: int) -> list[str]:
    return [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "BatchMode=yes",
        "-p",
        str(port),
    ]


def run_ssh(
    host: str,
    command: str,
    *,
    user: str = "root",
    private_key: str | None = None,
    port: int = 22,
    connect_timeout: int = 15,
    timeout: int = 300,
) -> SSHResult:
    """Run ``command`` on ``host`` over SSH. ``private_key`` is PEM text (written to a
    temp file with 0600 perms for the duration of the call).
    """
    key_path: str | None = None
    try:
        argv = ["ssh", *_base_opts(port=port, connect_timeout=connect_timeout)]
        if private_key:
            fd, key_path = tempfile.mkstemp(suffix=".pem")
            with open(fd, "w") as handle:
                handle.write(private_key if private_key.endswith("\n") else private_key + "\n")
            Path(key_path).chmod(0o600)
            argv += ["-i", key_path]
        argv += [f"{user}@{host}", command]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return SSHResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        return SSHResult(124, exc.stdout or "", f"ssh timeout after {timeout}s")
    finally:
        if key_path:
            try:
                Path(key_path).unlink(missing_ok=True)
            except OSError:
                pass


def upload_and_run_script(
    host: str,
    script: str,
    *,
    user: str = "root",
    private_key: str | None = None,
    port: int = 22,
    timeout: int = 300,
) -> SSHResult:
    """Pipe a script to a remote ``bash -s`` over SSH (no scp needed)."""
    remote = f"bash -s <<'LP_REMOTE_EOF'\n{script}\nLP_REMOTE_EOF\n"
    return run_ssh(
        host,
        remote,
        user=user,
        private_key=private_key,
        port=port,
        timeout=timeout,
    )


def build_remote_command(parts: Sequence[str]) -> str:
    """Safely join argv into a single remote command string."""
    return " ".join(shlex.quote(p) for p in parts)

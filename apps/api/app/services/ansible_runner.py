"""Run scaffolded Ansible playbooks against cloud VMs (inventory from IaC outputs)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

ANSIBLE_ROOT = Path("infra") / "ansible"


@dataclass(frozen=True, slots=True)
class AnsibleRunResult:
    status: str  # applied | skipped | failed
    detail: str = ""
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"applied", "skipped"}


def update_ansible_inventory_host(
    workspace_root: Path,
    *,
    host: str,
    ssh_user: str = "ubuntu",
    ssh_port: int = 22,
    ssh_private_key_path: str | None = None,
    inventory_group: str = "app_servers",
) -> Path | None:
    """Rewrite ``infra/ansible/inventory/hosts.yml`` for the provisioned VM."""
    inventory = workspace_root / ANSIBLE_ROOT / "inventory" / "hosts.yml"
    if not inventory.parent.is_dir():
        return None
    key_line = (
        f"        ansible_ssh_private_key_file: {ssh_private_key_path}\n"
        if ssh_private_key_path
        else ""
    )
    content = (
        "all:\n"
        "  children:\n"
        f"    {inventory_group}:\n"
        "      hosts:\n"
        f"        {host}:\n"
        "      vars:\n"
        f"        ansible_user: {ssh_user}\n"
        f"        ansible_port: {ssh_port}\n"
        f"{key_line}"
        "        ansible_python_interpreter: auto\n"
    )
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(content, encoding="utf-8")
    return inventory


def _resolve_ansible_playbook() -> str | None:
    """Return ansible-playbook path, installing ansible-core into a Launchpad venv if needed."""
    found = shutil.which("ansible-playbook")
    if found:
        return found
    home = Path.home()
    venv_bin = home / ".launchpad" / "tools" / "ansible-venv" / "bin" / "ansible-playbook"
    for candidate in (
        venv_bin,
        home / ".launchpad" / "tools" / "ansible" / "bin" / "ansible-playbook",
        home / ".local" / "bin" / "ansible-playbook",
        Path("/opt/homebrew/bin/ansible-playbook"),
        Path("/usr/local/bin/ansible-playbook"),
        Path(sys.executable).resolve().parent / "ansible-playbook",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    venv_dir = home / ".launchpad" / "tools" / "ansible-venv"
    try:
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        if not (venv_dir / "bin" / "python").is_file():
            create = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if create.returncode != 0:
                logger.warning(
                    "ansible_venv_create_failed",
                    detail=(create.stderr or create.stdout or "")[-400:],
                )
                return None
        pip = venv_dir / "bin" / "pip"
        if not pip.is_file():
            logger.warning("ansible_venv_missing_pip", path=str(venv_dir))
            return None
        install = subprocess.run(
            [
                str(pip),
                "install",
                "--quiet",
                "--upgrade",
                "pip",
                "ansible-core>=2.15,<2.19",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ansible_core_install_failed", detail=str(exc))
        return None
    if install.returncode != 0:
        logger.warning(
            "ansible_core_install_failed",
            detail=(install.stderr or install.stdout or "")[-400:],
        )
        return None
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        logger.info("ansible_core_installed", path=str(venv_bin))
        return str(venv_bin)
    found = shutil.which("ansible-playbook")
    if found:
        return found
    logger.warning("ansible_core_install_missing_binary", prefix=str(venv_dir))
    return None


def run_ansible_site(
    workspace_root: Path,
    *,
    timeout_seconds: float = 900.0,
    extra_env: dict[str, str] | None = None,
) -> AnsibleRunResult:
    """Install collections (best-effort) and run ``playbooks/site.yml``."""
    ansible_dir = workspace_root / ANSIBLE_ROOT
    playbook = ansible_dir / "playbooks" / "site.yml"
    if not playbook.is_file():
        return AnsibleRunResult("skipped", "no infra/ansible/playbooks/site.yml")

    ansible_bin = _resolve_ansible_playbook()
    if not ansible_bin or not Path(ansible_bin).exists():
        return AnsibleRunResult(
            "skipped",
            "ansible-playbook CLI not available "
            "(install ansible-core or allow auto-install under ~/.launchpad/tools)",
        )
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
    env.setdefault("ANSIBLE_RETRY_FILES_ENABLED", "False")
    env.setdefault("ANSIBLE_SSH_RETRIES", "3")
    env.setdefault(
        "ANSIBLE_SSH_ARGS",
        "-o BatchMode=yes -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null "
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=10 "
        "-o TCPKeepAlive=yes -o ConnectTimeout=20",
    )
    # Ensure sibling ansible-galaxy from the same install is visible.
    bin_dir = str(Path(ansible_bin).parent)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    requirements = ansible_dir / "requirements.yml"
    galaxy_bin = shutil.which("ansible-galaxy", path=env["PATH"])
    if requirements.is_file() and galaxy_bin:
        try:
            galaxy = subprocess.run(
                [
                    galaxy_bin,
                    "collection",
                    "install",
                    "-r",
                    str(requirements),
                    "-p",
                    str(ansible_dir / "collections"),
                ],
                cwd=str(ansible_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=min(timeout_seconds, 300.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("ansible_galaxy_install_failed", detail=str(exc))
        else:
            if galaxy.returncode != 0:
                logger.warning(
                    "ansible_galaxy_install_failed",
                    detail=(galaxy.stderr or galaxy.stdout)[-500:],
                )

    try:
        run = subprocess.run(
            [ansible_bin, "playbooks/site.yml"],
            cwd=str(ansible_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = ((exc.stderr or b"") + (exc.stdout or b"")).decode(
            "utf-8", errors="replace"
        )[-2000:]
        return AnsibleRunResult(
            "failed",
            f"ansible-playbook timed out after {int(timeout_seconds)}s",
            detail,
        )
    except OSError as exc:
        return AnsibleRunResult("failed", f"ansible-playbook failed to start: {exc}")

    combined = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()
    if run.returncode != 0:
        return AnsibleRunResult("failed", "ansible-playbook failed", combined[-3000:])
    logger.info("ansible_site_applied", workspace=str(workspace_root))
    return AnsibleRunResult("applied", "ansible-playbook complete", combined[-2000:])

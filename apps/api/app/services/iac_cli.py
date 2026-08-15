"""Resolve and bootstrap Terraform / Pulumi CLIs for worker apply/destroy.

Workers (local ``make worker`` and container images) must not depend on the
operator having ``pulumi`` on PATH. We look in common install locations, then
download the official Pulumi SDK tarball into ``~/.launchpad/tools/bin``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Pin so workers stay reproducible; bump intentionally with release notes.
DEFAULT_PULUMI_VERSION = "3.143.0"

_PULUMI_OS_ARCH: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "linux-x64",
    ("linux", "amd64"): "linux-x64",
    ("linux", "aarch64"): "linux-arm64",
    ("linux", "arm64"): "linux-arm64",
    ("darwin", "x86_64"): "darwin-x64",
    ("darwin", "amd64"): "darwin-x64",
    ("darwin", "arm64"): "darwin-arm64",
}


class IaCCliError(RuntimeError):
    """Raised when a required IaC CLI cannot be resolved or installed."""


def tools_bin_dir() -> Path:
    settings = get_settings()
    root = Path(settings.iac_workspace_root).expanduser().resolve().parent
    path = root / "tools" / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_pulumi_paths() -> list[Path]:
    candidates: list[Path] = []
    which = shutil.which("pulumi")
    if which:
        candidates.append(Path(which))
    home = Path.home()
    candidates.extend(
        [
            tools_bin_dir() / "pulumi",
            home / ".pulumi" / "bin" / "pulumi",
            Path("/usr/local/bin/pulumi"),
            Path("/opt/homebrew/bin/pulumi"),
        ]
    )
    return candidates


def resolve_pulumi_bin(*, install_if_missing: bool = True) -> str:
    """Return an absolute path to the ``pulumi`` binary.

    When ``install_if_missing`` is true and no binary is found, downloads the
    pinned release into ``~/.launchpad/tools/bin``.
    """
    for candidate in _candidate_pulumi_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    if not install_if_missing:
        raise IaCCliError("pulumi CLI not installed")

    installed = _install_pulumi_cli()
    return installed


def resolve_terraform_bin(*, prefer: str = "terraform") -> str | None:
    """Return terraform or tofu from PATH / tools bin, or None."""
    for name in (prefer, "terraform", "tofu"):
        which = shutil.which(name)
        if which:
            return which
        tool = tools_bin_dir() / name
        if tool.is_file() and os.access(tool, os.X_OK):
            return str(tool.resolve())
    return None


def ensure_pulumi_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copy env and prepend Launchpad / ~/.pulumi bin dirs to PATH."""
    env = dict(base or os.environ)
    extras = [
        str(tools_bin_dir()),
        str(Path.home() / ".pulumi" / "bin"),
    ]
    path = env.get("PATH", "")
    for extra in extras:
        if extra and extra not in path.split(os.pathsep):
            path = f"{extra}{os.pathsep}{path}" if path else extra
    env["PATH"] = path
    env.setdefault("PULUMI_SKIP_UPDATE_CHECK", "true")
    # Local file backend so workers need no Pulumi Cloud login.
    backend = Path.home() / ".pulumi" / "launchpad-backend"
    backend.mkdir(parents=True, exist_ok=True)
    env.setdefault("PULUMI_BACKEND_URL", f"file://{backend}")
    env.setdefault("PULUMI_CONFIG_PASSPHRASE", env.get("PULUMI_CONFIG_PASSPHRASE", "launchpad"))
    return env


def prepare_pulumi_project(pulumi_dir: Path, *, pulumi_bin: str, timeout: float) -> None:
    """npm install + select/create ``dev`` stack for a scaffolded project."""
    env = ensure_pulumi_env()
    package_json = pulumi_dir / "package.json"
    if package_json.is_file():
        npm = shutil.which("npm", path=env.get("PATH"))
        if npm is None:
            raise IaCCliError(
                "npm is required for Pulumi TypeScript projects but was not found on PATH"
            )
        node_modules = pulumi_dir / "node_modules"
        if not node_modules.is_dir():
            install = subprocess.run(
                [npm, "install", "--no-fund", "--no-audit"],
                cwd=str(pulumi_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=min(timeout, 600.0),
                check=False,
            )
            if install.returncode != 0:
                detail = (install.stderr or install.stdout or "")[-1500:]
                raise IaCCliError(f"npm install failed in {pulumi_dir}: {detail}")

    select = subprocess.run(
        [pulumi_bin, "stack", "select", "dev", "--create"],
        cwd=str(pulumi_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=min(timeout, 120.0),
        check=False,
    )
    if select.returncode != 0:
        detail = (select.stderr or select.stdout or "")[-1500:]
        raise IaCCliError(f"pulumi stack select failed: {detail}")


def pulumi_was_applied(pulumi_dir: Path) -> bool:
    """True when local project state indicates a prior ``pulumi up``."""
    if not pulumi_dir.is_dir():
        return False
    if (pulumi_dir / ".pulumi").is_dir():
        return True
    # File backend under home may still hold stacks keyed by project name.
    backend = Path.home() / ".pulumi" / "launchpad-backend"
    if backend.is_dir() and any(backend.rglob("*.json")):
        # Conservative: only if project Pulumi.yaml exists and node_modules was built
        # after a real apply attempt (stack select creates metadata under .pulumi).
        return (pulumi_dir / ".pulumi").is_dir()
    return False


def _pulumi_platform_slug() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    slug = _PULUMI_OS_ARCH.get((system, machine))
    if not slug:
        raise IaCCliError(f"unsupported platform for pulumi auto-install: {system}/{machine}")
    return slug


def _install_pulumi_cli() -> str:
    settings = get_settings()
    version = (
        getattr(settings, "pulumi_cli_version", None) or DEFAULT_PULUMI_VERSION
    ).lstrip("v")
    slug = _pulumi_platform_slug()
    dest_bin = tools_bin_dir() / "pulumi"
    if dest_bin.is_file() and os.access(dest_bin, os.X_OK):
        return str(dest_bin.resolve())

    url = (
        f"https://github.com/pulumi/pulumi/releases/download/v{version}/"
        f"pulumi-v{version}-{slug}.tar.gz"
    )
    fallback = (
        f"https://get.pulumi.com/releases/sdk/pulumi-v{version}-{slug}.tar.gz"
    )
    logger.info("pulumi_cli_install_start", version=version, url=url)

    with tempfile.TemporaryDirectory(prefix="launchpad-pulumi-") as tmp:
        tarball = Path(tmp) / "pulumi.tar.gz"
        last_error: Exception | None = None
        for candidate in (url, fallback):
            try:
                urllib.request.urlretrieve(candidate, tarball)  # noqa: S310 - fixed CDN URLs
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - try fallback mirror
                last_error = exc
                logger.warning("pulumi_cli_download_failed", url=candidate, error=str(exc))
        if last_error is not None or not tarball.is_file():
            raise IaCCliError(
                f"failed to download pulumi v{version}: {last_error}"
            ) from last_error

        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(extract_dir)

        # Tarball layout: pulumi/pulumi (+ plugins)
        found = list(extract_dir.rglob("pulumi"))
        binary = next(
            (p for p in found if p.is_file() and p.name == "pulumi" and os.access(p, os.X_OK)),
            None,
        )
        if binary is None:
            raise IaCCliError(f"pulumi binary missing from tarball {url}")

        tools = tools_bin_dir()
        for item in binary.parent.iterdir():
            if not item.is_file():
                continue
            target = tools / item.name
            shutil.copy2(item, target)
            target.chmod(target.stat().st_mode | 0o111)

    if not dest_bin.is_file():
        raise IaCCliError(f"pulumi install did not produce {dest_bin}")
    logger.info("pulumi_cli_install_ok", path=str(dest_bin), version=version)
    return str(dest_bin.resolve())

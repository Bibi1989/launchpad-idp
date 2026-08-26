"""VM bootstrap generator: cloud-init (user-data) + health/readiness polling.

Replaces Ansible *for the new plugin providers* with lightweight, OS-portable
``cloud-init`` that every major cloud (Hetzner, DigitalOcean, AWS, GCP, ...) accepts as
instance user-data. The generated config installs a container runtime, injects
environment variables/secrets, and registers a systemd unit that runs the app container
on boot. A separate health poller waits until the app answers over HTTP.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_APP_ENV_PATH = "/etc/launchpad/app.env"
_UNIT_PATH = "/etc/systemd/system/launchpad-app.service"
_UNIT_NAME = "launchpad-app.service"


def _env_file_content(env_vars: Mapping[str, str], *, app_port: int) -> str:
    merged: dict[str, str] = {"PORT": str(app_port), "HOST": "0.0.0.0"}
    for key, value in env_vars.items():
        clean_key = str(key).strip()
        if clean_key:
            merged[clean_key] = str(value).replace("\n", " ").replace("\r", " ")
    return "\n".join(f"{k}={v}" for k, v in merged.items()) + "\n"


def _systemd_unit(*, image: str, app_port: int) -> str:
    return (
        "[Unit]\n"
        "Description=Launchpad preview app\n"
        "After=docker.service network-online.target\n"
        "Requires=docker.service\n"
        "Wants=network-online.target\n"
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        "Restart=always\n"
        "RestartSec=3\n"
        "TimeoutStartSec=0\n"
        "ExecStartPre=-/usr/bin/docker rm -f launchpad-app\n"
        f"ExecStartPre=/usr/bin/docker pull {image}\n"
        "ExecStart=/usr/bin/docker run --rm --name launchpad-app "
        f"-p {app_port}:{app_port} --env-file {_APP_ENV_PATH} {image}\n"
        "ExecStop=/usr/bin/docker rm -f launchpad-app\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_cloud_init(
    *,
    image: str,
    app_port: int = 8080,
    env_vars: Mapping[str, str] | None = None,
    ssh_authorized_keys: Sequence[str] = (),
    extra_packages: Sequence[str] = (),
    package_upgrade: bool = True,
) -> str:
    """Return a ``#cloud-config`` YAML document (instance user-data).

    The instance will, on first boot: apply security updates, install Docker + the
    Compose plugin, write ``/etc/launchpad/app.env`` from ``env_vars``, install a systemd
    unit that runs ``image`` (published on ``app_port``, env-file injected), and start it.
    Idempotent to re-runs (systemd + ``docker rm -f`` guards).
    """
    env_vars = dict(env_vars or {})
    config: dict[str, object] = {
        "package_update": True,
        "package_upgrade": bool(package_upgrade),
        "packages": ["ca-certificates", "curl", "gnupg", *list(extra_packages)],
        "write_files": [
            {
                "path": _APP_ENV_PATH,
                "permissions": "0640",
                "content": _env_file_content(env_vars, app_port=app_port),
            },
            {
                "path": _UNIT_PATH,
                "permissions": "0644",
                "content": _systemd_unit(image=image, app_port=app_port),
            },
        ],
        "runcmd": [
            # Official convenience script installs Docker Engine + compose plugin on
            # Debian/Ubuntu/RHEL family. Skipped automatically if docker is preinstalled.
            "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sh)",
            "systemctl enable --now docker",
            "systemctl daemon-reload",
            f"docker pull {image} || true",
            f"systemctl enable --now {_UNIT_NAME}",
        ],
    }
    keys = [k for k in ssh_authorized_keys if str(k).strip()]
    if keys:
        config["ssh_authorized_keys"] = keys

    body = yaml.safe_dump(config, default_flow_style=False, sort_keys=False, width=4096)
    return "#cloud-config\n" + body


def render_docker_user_data_bash(
    *,
    image: str,
    app_port: int = 8080,
    env_vars: Mapping[str, str] | None = None,
) -> str:
    """Bash ``user-data`` alternative for images/providers without cloud-init support."""
    env_lines = _env_file_content(env_vars or {}, app_port=app_port)
    unit = _systemd_unit(image=image, app_port=app_port)
    return (
        "#!/bin/bash\n"
        "set -euxo pipefail\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sh)\n"
        "systemctl enable --now docker\n"
        "mkdir -p /etc/launchpad\n"
        f"cat > {_APP_ENV_PATH} <<'LP_ENV_EOF'\n{env_lines}LP_ENV_EOF\n"
        f"cat > {_UNIT_PATH} <<'LP_UNIT_EOF'\n{unit}LP_UNIT_EOF\n"
        "systemctl daemon-reload\n"
        f"docker pull {image} || true\n"
        f"systemctl enable --now {_UNIT_NAME}\n"
    )


def render_health_poll_script(*, app_port: int = 8080, health_path: str = "/") -> str:
    """Bash the operator can run over SSH: waits until the app answers locally."""
    path = health_path if health_path.startswith("/") else "/" + health_path
    return (
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        f"URL=\"http://127.0.0.1:{app_port}{path}\"\n"
        "for i in $(seq 1 60); do\n"
        "  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \"$URL\" || echo 000)\n"
        "  if [ \"$code\" -ge 200 ] && [ \"$code\" -lt 500 ]; then echo READY; exit 0; fi\n"
        "  sleep 5\n"
        "done\n"
        "echo TIMEOUT; exit 1\n"
    )


def poll_http_healthy(
    url: str,
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 5.0,
    now: object | None = None,
) -> bool:
    """Poll ``url`` from the control plane until it returns < 500, or time out.

    Uses httpx (already a dependency). Returns True on first healthy response.
    """
    import httpx

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        while True:
            try:
                resp = client.get(url)
                if resp.status_code < 500:
                    return True
            except Exception as exc:  # noqa: BLE001 - not up yet
                logger.debug("health_poll_not_ready", url=url, error=str(exc)[:200])
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.5, interval_seconds))

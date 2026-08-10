"""Generation of agent connection URLs and the host install script.

The control plane serves a ``/install.sh`` that a homelab operator pipes into a
shell with a single-use ``TOKEN``. The script runs the agent as a Docker
container with the host Docker socket mounted so it can manage workloads.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.core.config import Settings, get_settings

WS_CONNECT_PATH = "/api/v1/ws/nodes/connect"
REGISTER_PATH = "/api/v1/nodes/register"
INSTALL_PATH = "/install.sh"
BUNDLE_PATH = "/agent/bundle.tar.gz"

# Files that make up the agent Docker build context served to hosts with no registry.
_BUNDLE_FILES = ("Dockerfile", "requirements.txt", "__init__.py", "main.py", "runner.py")


def agent_source_dir() -> Path | None:
    """Locate the ``agent/`` build context on the control-plane filesystem."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    parents = here.parents
    if len(parents) > 4:
        candidates.append(parents[4] / "agent")  # monorepo: repo-root/agent
    candidates.append(Path("/app/agent"))  # slim OCI image layout
    candidates.append(Path.cwd() / "agent")
    for candidate in candidates:
        if (candidate / "main.py").is_file():
            return candidate
    return None


def build_agent_bundle() -> bytes:
    """Tar.gz the agent build context so a host can build the image without a registry."""
    src = agent_source_dir()
    if src is None:
        raise FileNotFoundError("agent source directory not found on the control plane")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in _BUNDLE_FILES:
            path = src / name
            if path.is_file():
                tar.add(str(path), arcname=name)
    return buffer.getvalue()


def control_plane_url(
    settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    """Public API origin the agent talks to.

    Precedence: explicit ``agent_control_plane_url`` setting, then the origin of the
    request that reached this endpoint, then ``public_app_url``. The API origin is
    required - the web app origin returns an HTML 404 for ``/install.sh``.
    """
    settings = settings or get_settings()
    if settings.agent_control_plane_url:
        return settings.agent_control_plane_url.rstrip("/")
    if request_base_url:
        return request_base_url.rstrip("/")
    return settings.public_app_url.rstrip("/")


def agent_ws_url(
    settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    """Public wss:// endpoint the agent dials back to."""
    settings = settings or get_settings()
    if settings.agent_ws_public_url:
        return f"{settings.agent_ws_public_url.rstrip('/')}{WS_CONNECT_PATH}"
    parts = urlsplit(control_plane_url(settings, request_base_url=request_base_url))
    ws_scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((ws_scheme, parts.netloc, WS_CONNECT_PATH, "", ""))


def register_url(settings: Settings | None = None, *, request_base_url: str | None = None) -> str:
    return f"{control_plane_url(settings, request_base_url=request_base_url)}{REGISTER_PATH}"


def one_line_install_command(
    token: str, settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    base = control_plane_url(settings, request_base_url=request_base_url)
    return f"curl -sSL {base}{INSTALL_PATH} | TOKEN={token} sh"


def render_install_script(
    settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    """POSIX shell installer. ``TOKEN`` (required) and ``LAUNCHPAD_URL`` come from env."""
    settings = settings or get_settings()
    base = control_plane_url(settings, request_base_url=request_base_url)
    image = settings.agent_image
    return f"""#!/bin/sh
# Launchpad hybrid agent installer.
# Usage: curl -sSL {base}{INSTALL_PATH} | TOKEN=lp_xxx sh
set -eu

LAUNCHPAD_URL="${{LAUNCHPAD_URL:-{base}}}"
LAUNCHPAD_AGENT_IMAGE="${{LAUNCHPAD_AGENT_IMAGE:-{image}}}"
CONTAINER_NAME="launchpad-agent"

if [ -z "${{TOKEN:-}}" ]; then
  echo "ERROR: TOKEN is required. Re-run with: curl -sSL {base}{INSTALL_PATH} | TOKEN=lp_xxx sh" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is required but was not found on PATH." >&2
  exit 1
fi

# Ensure the agent image exists: use a local image, else pull, else build from the
# source bundle the control plane serves (no registry / published image required).
ensure_image() {{
  if docker image inspect "$LAUNCHPAD_AGENT_IMAGE" >/dev/null 2>&1; then
    echo "==> Using local image $LAUNCHPAD_AGENT_IMAGE"
    return 0
  fi
  echo "==> Pulling agent image ($LAUNCHPAD_AGENT_IMAGE)"
  if docker pull "$LAUNCHPAD_AGENT_IMAGE" >/dev/null 2>&1; then
    return 0
  fi
  echo "==> Image not in a registry; building from control-plane source"
  workdir=$(mktemp -d)
  if ! curl -fsSL "$LAUNCHPAD_URL{BUNDLE_PATH}" | tar -xz -C "$workdir"; then
    echo "ERROR: could not download agent source from $LAUNCHPAD_URL{BUNDLE_PATH}" >&2
    rm -rf "$workdir"
    exit 1
  fi
  docker build -t "$LAUNCHPAD_AGENT_IMAGE" "$workdir"
  rm -rf "$workdir"
}}
ensure_image

echo "==> Removing any previous agent container"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

# Inside a container, localhost is the container itself, not the host running the
# control plane. Point the agent at the host via host.docker.internal so it can
# reach a control plane published on the host (e.g. http://localhost:8000 in dev).
AGENT_LAUNCHPAD_URL=$(printf '%s' "$LAUNCHPAD_URL" | sed -e 's#//localhost#//host.docker.internal#g' -e 's#//127.0.0.1#//host.docker.internal#g')

echo "==> Starting Launchpad agent -> $AGENT_LAUNCHPAD_URL"
docker run -d \\
  --name "$CONTAINER_NAME" \\
  --restart unless-stopped \\
  --add-host=host.docker.internal:host-gateway \\
  -e LAUNCHPAD_URL="$AGENT_LAUNCHPAD_URL" \\
  -e TOKEN="$TOKEN" \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v launchpad-agent-state:/var/lib/launchpad-agent \\
  "$LAUNCHPAD_AGENT_IMAGE"

echo "==> Agent started. Follow logs with: docker logs -f $CONTAINER_NAME"
"""

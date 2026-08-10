"""Launchpad hybrid local/edge agent.

Runs on a self-hosted / homelab host and dials an outbound WSS tunnel back to
the control plane. Requires no inbound ports or public IP. On first boot it
exchanges a single-use enrollment TOKEN for a durable per-node HMAC secret,
then streams telemetry heartbeats and executes deployment commands against the
local Docker daemon.

Environment:
    LAUNCHPAD_URL   control-plane base URL (e.g. https://launchpad.example.com)
    TOKEN           single-use enrollment token (lp_...) - only needed once
    AGENT_STATE_DIR where durable credentials are stored (default /var/lib/launchpad-agent)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets

from runner import DockerExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [agent] %(message)s",
)
logger = logging.getLogger("launchpad.agent")

AGENT_VERSION = "1.0.0"
DEFAULT_STATE_DIR = "/var/lib/launchpad-agent"
_RECONNECT_BASE_SECONDS = 2
_RECONNECT_MAX_SECONDS = 30


class AgentConfig:
    def __init__(self) -> None:
        self.control_plane_url = os.environ.get("LAUNCHPAD_URL", "").rstrip("/")
        self.enrollment_token = os.environ.get("TOKEN", "").strip()
        self.state_dir = Path(os.environ.get("AGENT_STATE_DIR", DEFAULT_STATE_DIR))
        if not self.control_plane_url:
            raise SystemExit("LAUNCHPAD_URL is required")

    @property
    def credentials_path(self) -> Path:
        return self.state_dir / "credentials.json"


class Credentials:
    def __init__(self, node_id: str, agent_secret: str, agent_ws_url: str, heartbeat: int) -> None:
        self.node_id = node_id
        self.agent_secret = agent_secret
        self.agent_ws_url = agent_ws_url
        self.heartbeat_interval_seconds = heartbeat

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "agent_secret": self.agent_secret,
            "agent_ws_url": self.agent_ws_url,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Credentials:
        return cls(
            node_id=data["node_id"],
            agent_secret=data["agent_secret"],
            agent_ws_url=data["agent_ws_url"],
            heartbeat=int(data.get("heartbeat_interval_seconds", 10)),
        )


def load_credentials(config: AgentConfig) -> Credentials | None:
    path = config.credentials_path
    if not path.exists():
        return None
    try:
        return Credentials.from_dict(json.loads(path.read_text()))
    except (ValueError, KeyError) as exc:
        logger.warning("ignoring invalid credentials file: %s", exc)
        return None


def save_credentials(config: AgentConfig, creds: Credentials) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    path = config.credentials_path
    path.write_text(json.dumps(creds.to_dict()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def register(config: AgentConfig, executor: DockerExecutor) -> Credentials:
    """Exchange the enrollment token for durable node credentials."""
    if not config.enrollment_token:
        raise SystemExit(
            "No stored credentials and TOKEN is not set. "
            "Re-run the installer with a fresh enrollment token."
        )
    metrics = executor.metrics()
    body = json.dumps(
        {
            "enrollment_token": config.enrollment_token,
            "hostname": platform.node(),
            "platform": f"{platform.system().lower()}/{platform.machine()}",
            "agent_version": AGENT_VERSION,
            "cpu_cores": metrics.get("cpu_cores"),
            "mem_total_mb": metrics.get("mem_total_mb"),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{config.control_plane_url}/api/v1/nodes/register",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - control-plane URL
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Registration failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach control plane: {exc}") from exc

    logger.info("registered as node %s", payload["node_id"])
    return Credentials(
        node_id=payload["node_id"],
        agent_secret=payload["agent_secret"],
        agent_ws_url=payload["agent_ws_url"],
        heartbeat=int(payload.get("heartbeat_interval_seconds", 10)),
    )


def effective_ws_url(server_ws_url: str, control_plane_url: str) -> str:
    """Choose a reachable WS endpoint.

    The control plane derives ``agent_ws_url`` from the request origin, which may be
    ``localhost`` (unreachable from inside the agent container). When so, rebuild it
    against the base the agent actually reached (``LAUNCHPAD_URL`` /
    ``host.docker.internal``). An explicit public host (e.g. a prod wss:// origin) is
    kept as-is.
    """
    parts = urlsplit(server_ws_url)
    if parts.hostname in ("localhost", "127.0.0.1"):
        base = urlsplit(control_plane_url)
        scheme = "wss" if base.scheme == "https" else "ws"
        return urlunsplit((scheme, base.netloc, parts.path, "", ""))
    return server_ws_url


def build_ws_url(creds: Credentials, control_plane_url: str) -> str:
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    message = f"{creds.node_id}.{ts}.{nonce}".encode()
    sig = hmac.new(creds.agent_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    query = urlencode({"node_id": creds.node_id, "ts": ts, "nonce": nonce, "sig": sig})
    base_ws = effective_ws_url(creds.agent_ws_url, control_plane_url)
    return f"{base_ws}?{query}"


async def _heartbeat_loop(ws: Any, executor: DockerExecutor, interval: int) -> None:
    while True:
        telemetry = executor.metrics()
        await ws.send(json.dumps({"type": "heartbeat", "telemetry": telemetry}))
        await asyncio.sleep(max(interval, 1))


def _execute_command(executor: DockerExecutor, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run a single command and return a NodeCommandResult-shaped dict."""
    try:
        if action == "pull_image":
            data = executor.pull_image(payload["image"])
        elif action == "run_container":
            data = executor.run_container(payload)
        elif action == "stop_container":
            data = executor.stop_container(payload["container"])
        elif action == "restart_container":
            data = executor.restart_container(payload["container"])
        elif action == "collect_logs":
            data = executor.collect_logs(payload["container"], int(payload.get("tail", 200)))
        elif action == "list_containers":
            data = executor.list_containers()
        else:
            return {"ok": False, "detail": f"unknown action: {action}", "data": {}}
        return {"ok": True, "detail": "", "data": data}
    except Exception as exc:  # noqa: BLE001 - report failure back to the control plane
        logger.warning("command %s failed: %s", action, exc)
        return {"ok": False, "detail": str(exc), "data": {}}


async def _serve(creds: Credentials, executor: DockerExecutor, control_plane_url: str) -> None:
    url = build_ws_url(creds, control_plane_url)
    async with websockets.connect(url, max_size=8 * 1024 * 1024, ping_interval=20) as ws:
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            raise RuntimeError(f"unexpected handshake: {ready}")
        interval = int(ready.get("heartbeat_interval_seconds", creds.heartbeat_interval_seconds))
        logger.info("tunnel established (heartbeat every %ss)", interval)

        heartbeat = asyncio.create_task(_heartbeat_loop(ws, executor, interval))
        try:
            async for raw in ws:
                frame = json.loads(raw)
                if frame.get("type") == "command":
                    command_id = frame.get("command_id")
                    action = frame.get("action", "")
                    payload = frame.get("payload", {}) or {}
                    logger.info("command %s (%s)", action, command_id)
                    result = _execute_command(executor, action, payload)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "command_result",
                                "command_id": command_id,
                                "result": {
                                    "command_id": command_id,
                                    "action": action,
                                    **result,
                                },
                            }
                        )
                    )
                elif frame.get("type") == "heartbeat_ack":
                    continue
        finally:
            heartbeat.cancel()


async def run() -> None:
    config = AgentConfig()
    executor = DockerExecutor()
    creds = load_credentials(config)
    if creds is None:
        creds = register(config, executor)
        save_credentials(config, creds)

    backoff = _RECONNECT_BASE_SECONDS
    while True:
        try:
            await _serve(creds, executor, config.control_plane_url)
            backoff = _RECONNECT_BASE_SECONDS
        except Exception as exc:  # noqa: BLE001 - reconnect on any tunnel error
            logger.warning("tunnel error: %s; reconnecting in %ss", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("agent stopped")


if __name__ == "__main__":
    main()

"""Per-preview cloudflared quick tunnels for remote "Open app" URLs.

Local previews are exposed as NodePort services on ``127.0.0.1:<node_port>`` — only
reachable from the host machine. When Launchpad itself is reached over a cloudflared
quick tunnel (``*.trycloudflare.com``), that tunnel only proxies its single local
port, so a preview's ``127.0.0.1:<port>`` URL cannot open remotely.

When ``PREVIEW_TUNNEL_MODE=cloudflared``, each local NodePort preview gets its own
``cloudflared tunnel --url http://127.0.0.1:<node_port>`` quick tunnel, yielding a
public ``https://<random>.trycloudflare.com`` URL that opens from anywhere — with no
Cloudflare account or domain required. Each app is served at the root of its own
hostname, so relative assets / SSR redirects keep working.

Tunnels are tracked in a JSON registry on disk so teardown (a *different* process
from the one that started them) can stop them, even across worker restarts.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def _settings(settings: Settings | None = None) -> Settings:
    return settings or get_settings()


def tunnel_mode(settings: Settings | None = None) -> str:
    return _settings(settings).preview_tunnel_mode


def tunnel_enabled(settings: Settings | None = None) -> bool:
    """True when preview tunnels are configured and the cloudflared binary exists."""
    import shutil

    cfg = _settings(settings)
    if cfg.preview_tunnel_mode != "cloudflared":
        return False
    return shutil.which(cfg.cloudflared_bin) is not None


def _state_dir(cfg: Settings) -> Path:
    configured = (cfg.preview_tunnel_state_dir or "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".launchpad"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _registry_path(cfg: Settings) -> Path:
    return _state_dir(cfg) / "preview-tunnels.json"


def _lock_path(cfg: Settings) -> Path:
    return _state_dir(cfg) / "preview-tunnels.lock"


class _FileLock:
    """Best-effort cross-process advisory lock around registry read/modify/write."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        try:
            import fcntl

            self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except Exception:  # noqa: BLE001 - locking is best-effort
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


def _load_registry(cfg: Settings) -> dict[str, dict]:
    path = _registry_path(cfg)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(cfg: Settings, registry: dict[str, dict]) -> None:
    path = _registry_path(cfg)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True))
    os.replace(tmp, path)  # atomic


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate(pid: int) -> None:
    """Terminate the cloudflared process group (started with a new session)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
            except OSError:
                return
        if not _pid_alive(pid):
            return
        time.sleep(0.5)


def _spawn_quick_tunnel(cfg: Settings, node_port: int) -> tuple[int, str] | None:
    """Launch a detached cloudflared quick tunnel; return (pid, log_path) or None."""
    log_fd, log_path = tempfile.mkstemp(prefix=f"launchpad-tunnel-{node_port}-", suffix=".log")
    try:
        proc = subprocess.Popen(
            [
                cfg.cloudflared_bin,
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{node_port}",
            ],
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group → survives worker, killable on teardown
        )
    except (OSError, ValueError) as exc:
        os.close(log_fd)
        logger.warning("preview_tunnel_spawn_failed", node_port=node_port, error=str(exc))
        return None
    finally:
        try:
            os.close(log_fd)
        except OSError:
            pass
    return proc.pid, log_path


def _read_url(log_path: str) -> str | None:
    try:
        match = _TRYCLOUDFLARE_RE.search(Path(log_path).read_text())
    except OSError:
        return None
    return match.group(0) if match else None


def start_preview_tunnel(
    *,
    environment_id: str,
    node_port: int,
    settings: Settings | None = None,
) -> str | None:
    """Ensure a cloudflared quick tunnel for this preview; return its public URL.

    Idempotent: if a live tunnel already serves this env's current port it is reused.
    Returns ``None`` (no error) when tunnels are disabled or the URL never appears —
    callers should fall back to the NodePort URL.
    """
    cfg = _settings(settings)
    if not tunnel_enabled(cfg):
        return None

    with _FileLock(_lock_path(cfg)):
        registry = _load_registry(cfg)
        existing = registry.get(environment_id)
        if existing:
            if (
                existing.get("node_port") == node_port
                and existing.get("url")
                and _pid_alive(int(existing.get("pid", -1)))
            ):
                return existing["url"]  # healthy tunnel for the same port → reuse
            # Stale or port changed → tear it down and recreate.
            _terminate(int(existing.get("pid", -1)))
            registry.pop(environment_id, None)
            _save_registry(cfg, registry)

    spawn = _spawn_quick_tunnel(cfg, node_port)
    if spawn is None:
        return None
    pid, log_path = spawn

    deadline = time.time() + max(cfg.preview_tunnel_timeout_seconds, 1.0)
    url: str | None = None
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        url = _read_url(log_path)
        if url:
            break
        time.sleep(0.5)

    if not url:
        logger.warning(
            "preview_tunnel_url_timeout",
            environment_id=environment_id,
            node_port=node_port,
        )
        _terminate(pid)
        try:
            os.unlink(log_path)
        except OSError:
            pass
        return None

    with _FileLock(_lock_path(cfg)):
        registry = _load_registry(cfg)
        registry[environment_id] = {
            "pid": pid,
            "url": url,
            "node_port": node_port,
            "log": log_path,
            "started_at": time.time(),
        }
        _save_registry(cfg, registry)

    logger.info(
        "preview_tunnel_started",
        environment_id=environment_id,
        node_port=node_port,
        url=url,
        pid=pid,
    )
    return url


def stop_preview_tunnel(environment_id: str, *, settings: Settings | None = None) -> bool:
    """Stop and forget this env's cloudflared tunnel. Best-effort; never raises."""
    cfg = _settings(settings)
    with _FileLock(_lock_path(cfg)):
        registry = _load_registry(cfg)
        entry = registry.pop(environment_id, None)
        if entry is not None:
            _save_registry(cfg, registry)
    if not entry:
        return False
    pid = int(entry.get("pid", -1))
    if pid > 0:
        _terminate(pid)
    log_path = entry.get("log")
    if log_path:
        try:
            os.unlink(log_path)
        except OSError:
            pass
    logger.info("preview_tunnel_stopped", environment_id=environment_id, pid=pid)
    return True

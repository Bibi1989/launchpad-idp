"""Per-preview cloudflared quick tunnel manager.

These tests fake the cloudflared process (no real tunnel is spawned): the spawn
helper just drops the trycloudflare URL into a log file, and pid liveness /
termination are stubbed, so we exercise the polling, registry, idempotency, and
teardown logic deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services import preview_tunnel as pt


def _settings(tmp_path: Path, mode: str = "cloudflared") -> Settings:
    return Settings(
        preview_tunnel_mode=mode,
        preview_tunnel_state_dir=str(tmp_path),
        preview_tunnel_timeout_seconds=3.0,
    )


@pytest.fixture
def fake_cloudflared(monkeypatch: pytest.MonkeyPatch):
    """Fake spawn + pid liveness so no real cloudflared runs."""
    # tunnel_enabled() does `import shutil; shutil.which(bin)` - patch the global.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _bin: "/usr/bin/cloudflared")

    spawned: list[int] = []
    terminated: list[int] = []
    counter = {"pid": 1000}

    def fake_spawn(cfg, node_port):
        counter["pid"] += 1
        pid = counter["pid"]
        log = tmp_dir[0] / f"tunnel-{pid}.log"
        log.write_text(
            "INF |  Your quick Tunnel has been created! Visit it at:  |\n"
            f"INF |  https://preview-{node_port}.trycloudflare.com  |\n"
        )
        spawned.append(pid)
        return pid, str(log)

    tmp_dir: list[Path] = []
    monkeypatch.setattr(pt, "_spawn_quick_tunnel", fake_spawn)
    monkeypatch.setattr(pt, "_pid_alive", lambda pid: pid not in terminated)
    monkeypatch.setattr(pt, "_terminate", lambda pid: terminated.append(pid))
    return {"spawned": spawned, "terminated": terminated, "tmp_dir": tmp_dir}


def test_disabled_returns_none(tmp_path: Path) -> None:
    url = pt.start_preview_tunnel(
        environment_id="env1", node_port=30081, settings=_settings(tmp_path, mode="off")
    )
    assert url is None


def test_start_creates_tunnel_and_registry(tmp_path: Path, fake_cloudflared) -> None:
    fake_cloudflared["tmp_dir"].append(tmp_path)
    cfg = _settings(tmp_path)

    url = pt.start_preview_tunnel(environment_id="env1", node_port=30081, settings=cfg)
    assert url == "https://preview-30081.trycloudflare.com"
    assert len(fake_cloudflared["spawned"]) == 1

    registry = pt._load_registry(cfg)
    assert registry["env1"]["url"] == url
    assert registry["env1"]["node_port"] == 30081


def test_start_is_idempotent_for_same_port(tmp_path: Path, fake_cloudflared) -> None:
    fake_cloudflared["tmp_dir"].append(tmp_path)
    cfg = _settings(tmp_path)

    url1 = pt.start_preview_tunnel(environment_id="env1", node_port=30081, settings=cfg)
    url2 = pt.start_preview_tunnel(environment_id="env1", node_port=30081, settings=cfg)
    assert url1 == url2
    assert len(fake_cloudflared["spawned"]) == 1  # reused, not re-spawned


def test_port_change_recreates_tunnel(tmp_path: Path, fake_cloudflared) -> None:
    fake_cloudflared["tmp_dir"].append(tmp_path)
    cfg = _settings(tmp_path)

    url1 = pt.start_preview_tunnel(environment_id="env1", node_port=30081, settings=cfg)
    url2 = pt.start_preview_tunnel(environment_id="env1", node_port=30082, settings=cfg)
    assert url1 != url2
    assert len(fake_cloudflared["spawned"]) == 2
    # the first tunnel's process was terminated on recreate
    assert fake_cloudflared["terminated"]
    assert pt._load_registry(cfg)["env1"]["node_port"] == 30082


def test_stop_terminates_and_forgets(tmp_path: Path, fake_cloudflared) -> None:
    fake_cloudflared["tmp_dir"].append(tmp_path)
    cfg = _settings(tmp_path)

    pt.start_preview_tunnel(environment_id="env1", node_port=30081, settings=cfg)
    stopped = pt.stop_preview_tunnel("env1", settings=cfg)
    assert stopped is True
    assert fake_cloudflared["terminated"]
    assert "env1" not in pt._load_registry(cfg)
    # stopping again is a no-op
    assert pt.stop_preview_tunnel("env1", settings=cfg) is False

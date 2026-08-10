"""Local execution engine for the Launchpad hybrid agent.

Wraps the host Docker daemon (via docker-py) and host telemetry (psutil) so the
control plane can inspect the machine and run/stop/inspect containerized
workloads. All methods return plain JSON-serializable dicts that map directly
onto the control-plane command protocol.
"""

from __future__ import annotations

import logging
import platform
from typing import Any

try:  # docker is required at runtime; import guarded for clearer error messages.
    import docker
    from docker.errors import DockerException, ImageNotFound, NotFound
except Exception:  # noqa: BLE001 - surfaced as docker_status=unavailable
    docker = None  # type: ignore[assignment]
    DockerException = ImageNotFound = NotFound = Exception  # type: ignore[assignment,misc]

try:
    import psutil
except Exception:  # noqa: BLE001 - metrics degrade gracefully without psutil
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger("launchpad.agent.runner")


class DockerExecutor:
    """Executes deployment commands against the local Docker engine."""

    def __init__(self) -> None:
        self._client = None
        if docker is not None:
            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:  # noqa: BLE001 - reported via docker_status
                logger.warning("docker unavailable: %s", exc)
                self._client = None

    # -- Telemetry --------------------------------------------------------- #

    def docker_status(self) -> str:
        if self._client is None:
            return "unavailable"
        try:
            self._client.ping()
            return "running"
        except Exception:  # noqa: BLE001
            return "unavailable"

    def metrics(self) -> dict[str, Any]:
        cpu_percent = mem_percent = disk_percent = 0.0
        cpu_cores: int | None = None
        mem_total_mb: int | None = None
        if psutil is not None:
            try:
                cpu_percent = float(psutil.cpu_percent(interval=None))
                vm = psutil.virtual_memory()
                mem_percent = float(vm.percent)
                mem_total_mb = int(vm.total / (1024 * 1024))
                disk_percent = float(psutil.disk_usage("/").percent)
                cpu_cores = psutil.cpu_count(logical=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("psutil metrics failed: %s", exc)
        return {
            "cpu_percent": round(min(max(cpu_percent, 0.0), 100.0), 2),
            "mem_percent": round(min(max(mem_percent, 0.0), 100.0), 2),
            "disk_percent": round(min(max(disk_percent, 0.0), 100.0), 2),
            "docker_status": self.docker_status(),
            "cpu_cores": cpu_cores,
            "mem_total_mb": mem_total_mb,
            "containers": self._list_container_summaries(),
        }

    def _list_container_summaries(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        try:
            containers = self._client.containers.list(all=False)
        except Exception:  # noqa: BLE001
            return []
        out: list[dict[str, Any]] = []
        for c in containers:
            image = ""
            try:
                image = c.image.tags[0] if c.image.tags else c.image.short_id
            except Exception:  # noqa: BLE001
                image = ""
            ports: list[str] = []
            try:
                for container_port, bindings in (c.attrs["NetworkSettings"]["Ports"] or {}).items():
                    if bindings:
                        for b in bindings:
                            ports.append(f"{b.get('HostPort')}->{container_port}")
                    else:
                        ports.append(container_port)
            except Exception:  # noqa: BLE001
                ports = []
            out.append(
                {
                    "id": c.short_id,
                    "name": c.name,
                    "image": image,
                    "status": c.status,
                    "ports": ports,
                }
            )
        return out

    # -- Commands ---------------------------------------------------------- #

    def require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Docker engine is not available on this host")
        return self._client

    def pull_image(self, image: str) -> dict[str, Any]:
        client = self.require_client()
        img = client.images.pull(image)
        tags = getattr(img, "tags", []) or [image]
        return {"image": tags[0], "id": getattr(img, "short_id", "")}

    def run_container(self, spec: dict[str, Any]) -> dict[str, Any]:
        client = self.require_client()
        name = spec["name"]
        image = spec["image"]

        if spec.get("pull", True):
            try:
                client.images.pull(image)
            except ImageNotFound:
                pass  # fall through; run will fail with a clear error if truly missing

        # Idempotent replace: remove any container occupying the name.
        try:
            existing = client.containers.get(name)
            existing.remove(force=True)
        except NotFound:
            pass

        ports: dict[str, int] = {}
        for p in spec.get("ports", []) or []:
            proto = p.get("protocol", "tcp")
            ports[f"{p['container_port']}/{proto}"] = int(p["host_port"])

        volumes: dict[str, dict[str, str]] = {}
        for v in spec.get("volumes", []) or []:
            volumes[v["host_path"]] = {"bind": v["container_path"], "mode": v.get("mode", "rw")}

        kwargs: dict[str, Any] = {
            "name": name,
            "detach": True,
            "environment": spec.get("env", {}) or {},
            "ports": ports,
            "volumes": volumes,
            "restart_policy": {"Name": spec.get("restart_policy", "unless-stopped")},
        }
        if spec.get("cpu_limit"):
            kwargs["nano_cpus"] = int(float(spec["cpu_limit"]) * 1_000_000_000)
        if spec.get("memory_mb"):
            kwargs["mem_limit"] = f"{int(spec['memory_mb'])}m"
        if spec.get("command"):
            kwargs["command"] = spec["command"]

        container = client.containers.run(image, **kwargs)
        return {"container_id": container.short_id, "name": container.name, "status": container.status}

    def stop_container(self, ref: str) -> dict[str, Any]:
        client = self.require_client()
        container = client.containers.get(ref)
        container.stop(timeout=10)
        return {"container": container.name, "status": "stopped"}

    def restart_container(self, ref: str) -> dict[str, Any]:
        client = self.require_client()
        container = client.containers.get(ref)
        container.restart(timeout=10)
        return {"container": container.name, "status": container.status}

    def collect_logs(self, ref: str, tail: int = 200) -> dict[str, Any]:
        client = self.require_client()
        container = client.containers.get(ref)
        raw = container.logs(tail=tail, timestamps=True)
        return {"container": container.name, "logs": raw.decode("utf-8", errors="replace")}

    def list_containers(self) -> dict[str, Any]:
        return {"containers": self._list_container_summaries()}

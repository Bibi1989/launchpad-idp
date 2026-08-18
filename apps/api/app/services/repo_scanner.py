"""Zero-config auto-discovery: extract service names and exposed ports from a repo.

Parses ``Dockerfile`` (``EXPOSE``) and ``docker-compose.yml`` (service names +
port mappings) so a workspace can be seeded without the operator hand-typing
ports. This is additive: callers opt in by calling :func:`scan_repo`, and a
``manual_ports`` override always wins so existing, manually configured repos keep
working unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

# Mirrors services.attach_deploy._EXPOSE_RE but captures the FULL operand so
# multi-port lines (``EXPOSE 8080 9090/tcp``) are handled.
_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(.+)$", re.MULTILINE | re.IGNORECASE)

_COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_DOCKERFILE_GLOBS = ("Dockerfile", "Dockerfile.*", "*.Dockerfile")


class ScannedService(BaseModel):
    """A service discovered in the repo, with its container ports."""

    name: str = Field(min_length=1, max_length=128)
    ports: list[int] = Field(default_factory=list)
    source: str = Field(description="dockerfile | compose")
    dockerfile: str | None = None
    image: str | None = None


class RepoScanResult(BaseModel):
    services: list[ScannedService] = Field(default_factory=list)
    compose_file: str | None = None
    dockerfiles: list[str] = Field(default_factory=list)

    @property
    def all_ports(self) -> list[int]:
        seen: dict[int, None] = {}
        for svc in self.services:
            for port in svc.ports:
                seen.setdefault(port, None)
        return list(seen.keys())


def parse_expose_ports(dockerfile_text: str) -> list[int]:
    """Extract container ports from all ``EXPOSE`` lines (deduped, ordered)."""
    ports: list[int] = []
    seen: set[int] = set()
    for operand in _EXPOSE_RE.findall(dockerfile_text or ""):
        for token in operand.split():
            # ``8080`` or ``8080/tcp`` (ignore ${VARS} we cannot resolve).
            raw = token.split("/", 1)[0].strip()
            if not raw.isdigit():
                continue
            port = int(raw)
            if 1 <= port <= 65535 and port not in seen:
                seen.add(port)
                ports.append(port)
    return ports


def _parse_compose_ports(value: object) -> list[int]:
    """Extract CONTAINER (target) ports from a compose ``ports``/``expose`` list."""
    ports: list[int] = []
    seen: set[int] = set()

    def _add(port: int) -> None:
        if 1 <= port <= 65535 and port not in seen:
            seen.add(port)
            ports.append(port)

    if not isinstance(value, list):
        return ports
    for entry in value:
        target: int | None = None
        if isinstance(entry, dict):
            # Long form: {target: 80, published: 8080}
            raw = entry.get("target")
            if isinstance(raw, int):
                target = raw
            elif isinstance(raw, str) and raw.isdigit():
                target = int(raw)
        elif isinstance(entry, int):
            target = entry
        elif isinstance(entry, str):
            # Short form: "8080:80", "80", "127.0.0.1:8080:80", "80/tcp".
            spec = entry.split("/", 1)[0]
            parts = spec.split(":")
            tail = parts[-1].strip()
            # A range ("8080-8090") -> take the first.
            tail = tail.split("-", 1)[0]
            if tail.isdigit():
                target = int(tail)
        if target is not None:
            _add(target)
    return ports


def scan_repo(
    root: str | Path,
    *,
    include_dockerfile: bool = True,
    include_compose: bool = True,
    manual_ports: list[int] | None = None,
) -> RepoScanResult:
    """Auto-discover services + exposed ports from a repository tree.

    ``manual_ports`` (an explicit, operator-provided list) takes precedence and is
    returned as-is under a synthetic ``app`` service, so existing manually
    configured repos keep their behavior. Best-effort: unreadable/invalid files
    are skipped, never raised.
    """
    root_path = Path(root)

    if manual_ports:
        return RepoScanResult(
            services=[ScannedService(name="app", ports=list(manual_ports), source="manual")]
        )

    result = RepoScanResult()

    if include_compose:
        compose_path = next(
            (root_path / name for name in _COMPOSE_FILENAMES if (root_path / name).is_file()),
            None,
        )
        if compose_path is not None:
            result.compose_file = compose_path.name
            result.services.extend(_scan_compose(compose_path))

    if include_dockerfile:
        # Only add Dockerfile-derived services compose did not already cover.
        covered = {svc.name for svc in result.services}
        for df in _find_dockerfiles(root_path):
            rel = str(df.relative_to(root_path))
            result.dockerfiles.append(rel)
            name = _dockerfile_service_name(df, root_path)
            if name in covered:
                continue
            try:
                ports = parse_expose_ports(df.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            result.services.append(
                ScannedService(name=name, ports=ports, source="dockerfile", dockerfile=rel)
            )
            covered.add(name)

    return result


def _scan_compose(compose_path: Path) -> list[ScannedService]:
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("repo_scan_compose_parse_failed", file=str(compose_path), error=str(exc))
        return []
    if not isinstance(data, dict):
        return []
    services = data.get("services")
    if not isinstance(services, dict):
        return []
    out: list[ScannedService] = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        ports = _parse_compose_ports(spec.get("ports"))
        # ``expose`` lists container-only ports (not published).
        for port in _parse_compose_ports(spec.get("expose")):
            if port not in ports:
                ports.append(port)
        image = spec.get("image") if isinstance(spec.get("image"), str) else None
        out.append(
            ScannedService(name=str(name)[:128], ports=ports, source="compose", image=image)
        )
    return out


def _find_dockerfiles(root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in _DOCKERFILE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                found.append(path)
    # One level of app subdirectories (monorepo apps/<svc>/Dockerfile).
    for sub in sorted(root.glob("*/Dockerfile")) + sorted(root.glob("apps/*/Dockerfile")):
        if sub.is_file() and sub not in seen:
            seen.add(sub)
            found.append(sub)
    return found


def _dockerfile_service_name(dockerfile: Path, root: Path) -> str:
    """Derive a stable service name from a Dockerfile's location."""
    parent = dockerfile.parent
    if parent == root:
        # Root Dockerfile or Dockerfile.<suffix> -> use suffix if present.
        if dockerfile.name.lower() != "dockerfile" and "." in dockerfile.name:
            suffix = dockerfile.name.replace("Dockerfile.", "").replace(".Dockerfile", "")
            return re.sub(r"[^a-z0-9-]", "-", suffix.lower()).strip("-") or "app"
        return "app"
    return re.sub(r"[^a-z0-9-]", "-", parent.name.lower()).strip("-") or "app"

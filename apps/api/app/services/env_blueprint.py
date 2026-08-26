"""Per-service env blueprint loading for preview deploys (pods / Docker / VMs).

Blueprint files (preferred order):
1. ``.env.launchpad`` (future product default)
2. ``.env.example`` (current documented blueprint)
3. ``.env.sample`` / ``.env.template`` / ``env.example``

Merge precedence at inject time (low → high):
blueprint → connection_env → datastore → wizard env_vars → CORS / frontend API URL.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.logging import get_logger
from pkg.detector.env_example import (
    ENV_BLUEPRINT_FILENAMES,
    collect_env_blueprint_map,
    discover_env_example_files,
)

logger = get_logger(__name__)

# Documented current blueprint name. Prefer writing this until product renames
# to ``.env.launchpad`` (already first in ENV_BLUEPRINT_FILENAMES for reads).
DEFAULT_ENV_BLUEPRINT_WRITE_NAME = ".env.example"

_STUB_HEADER = """\
# Launchpad environment blueprint for preview deploys.
# Prefer this file (or .env.launchpad) so Launchpad can inject keys into
# Kubernetes pods, Docker containers, and VM instances.
# Secrets should stay blank here; set real values in the Launchpad UI or vault.
#
# Rename path: .env.example -> .env.launchpad (Launchpad already reads both).
"""


def has_env_blueprint(service_root: Path) -> bool:
    root = Path(service_root)
    return any((root / name).is_file() for name in ENV_BLUEPRINT_FILENAMES)


def load_service_env_blueprint(service_root: Path) -> dict[str, str]:
    """Load non-secret KEY=VALUE defaults from a service/repo root."""
    root = Path(service_root)
    if not root.is_dir():
        return {}
    return collect_env_blueprint_map(root, include_secrets=False)


# Env keys that signal a service needs a relational DB / Redis. Used to auto-provision
# an in-cluster datastore for linked repos whose committed .env.example points at a
# compose-only host (e.g. ``db``) that does not resolve in the k8s preview.
_POSTGRES_URL_KEYS = ("DATABASE_URL", "DB_URL", "POSTGRES_URL", "POSTGRESQL_URL", "PG_URL")
_POSTGRES_HOST_KEYS = ("POSTGRES_HOST", "PGHOST", "DB_HOST", "DATABASE_HOST")
_REDIS_KEYS = ("REDIS_URL", "REDIS_HOST", "REDIS_URI")


def _value_is_postgres(value: str) -> bool:
    low = value.strip().lower()
    return low.startswith(("postgres://", "postgresql://")) or "postgres" in low


def _value_is_mysql(value: str) -> bool:
    low = value.strip().lower()
    return low.startswith(("mysql://", "mariadb://"))


def workspace_datastore_needs(workspace_root: Path) -> tuple[bool, bool]:
    """Detect whether any service blueprint in the workspace needs Postgres / Redis.

    Scans discovered ``.env.example`` / ``.env.launchpad`` files (repo root + common
    app dirs) for DB/Redis connection keys. Returns ``(needs_postgres, needs_redis)``.
    A relational URL that is explicitly MySQL/MariaDB does not request Postgres (the
    ephemeral datastore only provides Postgres/Redis).
    """
    root = Path(workspace_root)
    if not root.is_dir():
        return (False, False)
    needs_pg = False
    needs_redis = False
    for path in discover_env_example_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        upper = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            upper[key.strip().upper()] = value.strip().strip('"').strip("'")
        for key in _POSTGRES_URL_KEYS:
            if key in upper:
                val = upper[key]
                if _value_is_mysql(val):
                    continue
                needs_pg = True
        if not needs_pg and any(k in upper for k in _POSTGRES_HOST_KEYS):
            # A discrete DB host without an explicit MySQL URL: assume Postgres.
            if not any(_value_is_mysql(upper.get(k, "")) for k in _POSTGRES_URL_KEYS):
                needs_pg = True
        if any(k in upper for k in _REDIS_KEYS):
            needs_redis = True
    return (needs_pg, needs_redis)


def ensure_env_blueprint_stub(
    service_root: Path,
    *,
    keys: dict[str, str] | None = None,
    filename: str = DEFAULT_ENV_BLUEPRINT_WRITE_NAME,
) -> Path | None:
    """Create a blueprint file when none exists so users know the contract.

    Does not overwrite an existing blueprint. Returns the path written, or None.
    """
    root = Path(service_root)
    if not root.is_dir() or has_env_blueprint(root):
        return None
    target = root / filename
    lines = [_STUB_HEADER.rstrip(), ""]
    for key, value in (keys or {}).items():
        cleaned = str(key or "").strip()
        if not cleaned:
            continue
        lines.append(f"{cleaned}={value}")
    if len(lines) <= 2:
        lines.extend(
            [
                "NODE_ENV=production",
                "PORT=",
                "DATABASE_URL=",
                "API_URL=",
                "VITE_API_URL=",
                "CORS_ALLOWED_ORIGINS=",
            ]
        )
    try:
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "env_blueprint_stub_write_failed",
            path=str(target),
            error=str(exc)[:200],
        )
        return None
    logger.info("env_blueprint_stub_written", path=str(target))
    return target


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _read_image_build_contexts(workspace_root: Path) -> list[tuple[str, Path]]:
    plan_path = Path(workspace_root) / ".launchpad" / "image-builds.json"
    if not plan_path.is_file():
        return []
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, Path]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        image = str(entry.get("image") or entry.get("service") or "").strip()
        ctx = str(entry.get("context") or ".").strip() or "."
        if not image:
            continue
        path = Path(workspace_root) if ctx in {".", ""} else Path(workspace_root) / ctx
        out.append((image, path))
    return out


def iter_service_roots(workspace_root: Path) -> list[tuple[str, Path]]:
    """Return ``(service_key, path)`` for workspace services that may have blueprints."""
    root = Path(workspace_root).resolve()
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def _add(key: str, path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.is_dir() or resolved in seen:
            return
        seen.add(resolved)
        found.append((key, resolved))

    _add("workspace", root)
    for image, ctx in _read_image_build_contexts(root):
        key = _slug(image.split(":")[0]) or "app"
        _add(key, ctx)

    apps = root / "apps"
    if apps.is_dir():
        for child in sorted(apps.iterdir()):
            if child.is_dir():
                _add(_slug(child.name) or child.name, child)

    for sub in ("services", "backend", "api", "web", "frontend"):
        base = root / sub
        if base.is_dir():
            _add(_slug(sub) or sub, base)

    return found


def load_workspace_service_blueprints(workspace_root: Path) -> dict[str, dict[str, str]]:
    """Map service key → blueprint env for every discovered service root."""
    out: dict[str, dict[str, str]] = {}
    for key, path in iter_service_roots(workspace_root):
        blueprint = load_service_env_blueprint(path)
        if blueprint:
            out[key] = blueprint
    return out


def resolve_blueprint_for_name(
    workspace_root: Path,
    name: str,
) -> dict[str, str]:
    """Best-effort blueprint for a Deployment / compose service / image name."""
    root = Path(workspace_root)
    needle = _slug(name)
    if not needle:
        return load_service_env_blueprint(root)

    blueprints = load_workspace_service_blueprints(root)
    if needle in blueprints:
        return dict(blueprints[needle])

    for key, env in blueprints.items():
        if key == "workspace":
            continue
        if needle in key or key in needle:
            return dict(env)
        # launch-test-frontend <-> launch-test-frontend-service
        if needle.removesuffix("-service") == key or key.removesuffix("-service") == needle:
            return dict(env)

    # Direct apps/<name> / context path match.
    for candidate in (
        root / "apps" / name,
        root / "apps" / needle,
        root / name,
        root / needle,
    ):
        if candidate.is_dir():
            loaded = load_service_env_blueprint(candidate)
            if loaded:
                return loaded

    return load_service_env_blueprint(root)


def merge_env_layers(*layers: dict[str, str] | None) -> dict[str, str]:
    """Merge env maps left→right; later non-empty values overwrite earlier ones."""
    merged: dict[str, str] = {}
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            cleaned_key = str(key or "").strip()
            if not cleaned_key:
                continue
            cleaned_val = "" if value is None else str(value)
            if cleaned_val == "" and cleaned_key in merged:
                continue
            merged[cleaned_key] = cleaned_val
    return merged


def apply_blueprints_to_build_env(
    service_root: Path,
    build_env: dict[str, str] | None,
) -> dict[str, str]:
    """Blueprint first, then build_env (connection / platform) wins."""
    ensure_env_blueprint_stub(service_root)
    return merge_env_layers(load_service_env_blueprint(service_root), build_env)


# Env keys passed as ``docker build --build-arg`` (must match Dockerfile ARG when present).
_BUILD_TIME_ENV_PREFIXES: tuple[str, ...] = (
    "VITE_",
    "NEXT_PUBLIC_",
    "NUXT_PUBLIC_",
    "VUE_APP_",
    "NG_APP_",
    "PUBLIC_",
)
_BUILD_TIME_ENV_KEYS: frozenset[str] = frozenset({"API_URL", "BACKEND_URL"})


def docker_build_args_from_env(env: dict[str, str] | None) -> list[str]:
    """Flatten env into docker ``--build-arg KEY=VALUE`` pairs for SPA/SSR builds."""
    if not env:
        return []
    args: list[str] = []
    for key, value in env.items():
        cleaned_key = str(key or "").strip()
        if not cleaned_key or value is None:
            continue
        cleaned_val = str(value).strip()
        if not cleaned_val:
            continue
        if cleaned_key.startswith(_BUILD_TIME_ENV_PREFIXES) or cleaned_key in _BUILD_TIME_ENV_KEYS:
            args.extend(["--build-arg", f"{cleaned_key}={cleaned_val}"])
    return args


def inject_blueprints_into_documents(
    documents: list[dict],
    *,
    workspace_root: Path,
    inject_fn,
) -> None:
    """Apply per-Deployment blueprint env via ``inject_fn(docs, env, only_*=...)``.

    ``inject_fn`` is ``inject_extra_env_into_documents`` (imported by caller to
    avoid circular imports).
    """
    root = Path(workspace_root)
    for doc in documents:
        if not isinstance(doc, dict) or str(doc.get("kind") or "") != "Deployment":
            continue
        name = str((doc.get("metadata") or {}).get("name") or "").strip()
        if not name:
            continue
        blueprint = resolve_blueprint_for_name(root, name)
        if not blueprint:
            continue
        # setdefault semantics: only fill keys missing on the container by
        # injecting a filtered map after platform env was applied. Callers should
        # invoke this BEFORE higher-priority injects, or pass filtered keys.
        inject_fn([doc], blueprint)

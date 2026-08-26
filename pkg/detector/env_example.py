"""Parse ``.env.example`` / ``.env.launchpad`` files for import-time env configuration."""

from __future__ import annotations

import re
from pathlib import Path

from pkg.detector.models import EnvExampleVar

# Preferred first. Renaming the product default later is a one-line reorder:
# keep ``.env.launchpad`` first when that becomes the documented blueprint.
ENV_BLUEPRINT_FILENAMES: tuple[str, ...] = (
    ".env.launchpad",
    ".env.example",
    ".env.sample",
    ".env.template",
    "env.example",
)

# Back-compat alias used by older call sites / docs.
_ENV_FILE_NAMES = ENV_BLUEPRINT_FILENAMES

_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|auth)",
    re.IGNORECASE,
)

_LINE_RE = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)


def _looks_secret(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def _strip_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"} and value[-1] == value[0] and len(value) >= 2:
        return value[1:-1]
    # Drop inline comments for unquoted values: KEY=foo # bar
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def parse_env_example_text(text: str, *, source: str = ".env.example") -> list[EnvExampleVar]:
    """Parse dotenv-example content into ordered unique keys."""
    vars_out: list[EnvExampleVar] = []
    seen: set[str] = set()
    pending_comment = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            pending_comment = ""
            continue
        if stripped.startswith("#"):
            pending_comment = stripped.lstrip("#").strip()
            continue
        match = _LINE_RE.match(stripped)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        example = _strip_value(match.group(2))
        secret = _looks_secret(key)
        vars_out.append(
            EnvExampleVar(
                key=key,
                example_value="" if secret else example,
                comment=pending_comment or None,
                source=source,
                is_secret=secret,
                suggested_value="" if secret else example,
            )
        )
        pending_comment = ""
    return vars_out


def discover_env_example_files(root: Path, *, max_files: int = 8) -> list[Path]:
    """Find env blueprint files at repo root and one level of common app dirs."""
    root = root.resolve()
    found: list[Path] = []
    for name in ENV_BLUEPRINT_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
    for sub in ("apps", "packages", "services", "backend", "api", "web", "frontend"):
        base = root / sub
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            for name in ENV_BLUEPRINT_FILENAMES:
                candidate = child / name
                if candidate.is_file():
                    found.append(candidate)
        for name in ENV_BLUEPRINT_FILENAMES:
            candidate = base / name
            if candidate.is_file():
                found.append(candidate)
        if len(found) >= max_files:
            break
    # Deduplicate while preserving order
    out: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
        if len(out) >= max_files:
            break
    return out


def collect_env_example_vars(root: Path) -> list[EnvExampleVar]:
    """Merge env blueprint vars from discovered files (first key wins)."""
    merged: list[EnvExampleVar] = []
    seen: set[str] = set()
    for path in discover_env_example_files(root):
        try:
            rel = str(path.relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            rel = path.name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for item in parse_env_example_text(text, source=rel):
            if item.key in seen:
                continue
            seen.add(item.key)
            merged.append(item)
    return merged


def collect_env_blueprint_map(
    root: Path,
    *,
    include_secrets: bool = False,
) -> dict[str, str]:
    """Return ``KEY -> suggested_value`` from the service/repo env blueprint.

    Secrets stay blank unless ``include_secrets`` is True (preview inject should
    not invent secret values from example placeholders).
    """
    out: dict[str, str] = {}
    for item in collect_env_example_vars(root):
        if item.is_secret and not include_secrets:
            continue
        value = (item.suggested_value or item.example_value or "").strip()
        if value:
            out[item.key] = value
    return out


def suggested_datastore_urls(kind: str, *, app_name: str = "app") -> dict[str, str]:
    """Recommended connection strings for in-cluster vs external placement."""
    db = re.sub(r"[^a-z0-9_]+", "_", app_name.lower()).strip("_") or "app"
    kind = kind.strip().lower()
    if kind == "postgres":
        return {
            "in_cluster": f"postgresql://launchpad:changeme@postgres:5432/{db}",
            "external": "postgresql://USER:PASSWORD@HOST:5432/DBNAME",
        }
    if kind == "mysql":
        return {
            "in_cluster": f"mysql://launchpad:changeme@mysql:3306/{db}",
            "external": "mysql://USER:PASSWORD@HOST:3306/DBNAME",
        }
    if kind == "mariadb":
        return {
            "in_cluster": f"mysql://launchpad:changeme@mariadb:3306/{db}",
            "external": "mysql://USER:PASSWORD@HOST:3306/DBNAME",
        }
    if kind == "mongodb":
        return {
            "in_cluster": f"mongodb://launchpad:changeme@mongodb:27017/{db}?authSource=admin",
            "external": "mongodb+srv://USER:PASSWORD@HOST/DBNAME",
        }
    if kind == "redis":
        return {
            "in_cluster": "redis://redis:6379/0",
            "external": "redis://default:PASSWORD@HOST:6379/0",
        }
    return {"in_cluster": "", "external": ""}

"""Safe CRUD helpers for provisioning workspace files on disk."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

_SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9_./@+-]+$")
_MAX_FILE_BYTES = 2_000_000
_DENIED_PATH_SEGMENTS = frozenset(
    {
        ".launchpad",
        ".git",
        ".terraform",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "coverage",
        ".turbo",
        ".next",
        ".nuxt",
        ".output",
        "dist",
        "build",
        "bin",
    }
)
_DENIED_SUFFIXES = (
    ".tfstate",
    ".tfstate.backup",
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".o",
)


class WorkspaceFileError(ValueError):
    """Raised when a path or content operation is invalid."""


def is_denied_workspace_path(relative: Path) -> bool:
    """Return True when a relative path must not be exposed via the IDE or bundles."""
    name = relative.name
    if name.endswith(_DENIED_SUFFIXES) or name.endswith(".tfstate"):
        return True
    for part in relative.parts:
        if part in _DENIED_PATH_SEGMENTS:
            return True
        if part == ".env" or part.startswith(".env."):
            return True
    return False


def _is_hidden(relative: Path) -> bool:
    return is_denied_workspace_path(relative)


def resolve_safe_path(workspace_dir: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``workspace_dir`` with traversal guards."""
    cleaned = relative_path.strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        raise WorkspaceFileError("Path is required")
    if cleaned in {".", ".."} or ".." in cleaned.split("/"):
        raise WorkspaceFileError("Path traversal is not allowed")
    if not _SAFE_RELATIVE.match(cleaned):
        raise WorkspaceFileError(
            "Path may only contain letters, numbers, and _ . / @ + -"
        )
    target = (workspace_dir / cleaned).resolve()
    root = workspace_dir.resolve()
    if target != root and root not in target.parents:
        raise WorkspaceFileError("Path escapes workspace root")
    relative = target.relative_to(root)
    if _is_hidden(relative):
        raise WorkspaceFileError("Hidden paths are not accessible")
    return target


def list_file_tree(workspace_dir: Path) -> list[dict[str, object]]:
    """Return a flat sorted list of file/dir nodes (relative paths)."""
    root = workspace_dir.resolve()
    nodes: list[dict[str, object]] = []
    if not root.is_dir():
        return nodes

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _is_hidden(relative):
            continue
        rel = str(relative).replace("\\", "/")
        if path.is_dir():
            nodes.append({"path": rel, "type": "directory", "size": None})
        elif path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            nodes.append({"path": rel, "type": "file", "size": size})
    return nodes


def read_file(workspace_dir: Path, relative_path: str) -> str:
    target = resolve_safe_path(workspace_dir, relative_path)
    if not target.is_file():
        raise WorkspaceFileError(f"File not found: {relative_path}")
    if target.stat().st_size > _MAX_FILE_BYTES:
        raise WorkspaceFileError("File exceeds maximum editable size (2MB)")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceFileError("Binary files cannot be edited in the IDE") from exc


def write_file(
    workspace_dir: Path,
    relative_path: str,
    content: str,
    *,
    create_parents: bool = True,
) -> str:
    target = resolve_safe_path(workspace_dir, relative_path)
    if target.exists() and target.is_dir():
        raise WorkspaceFileError("Cannot write file: path is a directory")
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        raise WorkspaceFileError("Content exceeds maximum size (2MB)")
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    elif not target.parent.is_dir():
        raise WorkspaceFileError("Parent directory does not exist")
    target.write_text(content, encoding="utf-8")
    return str(target.relative_to(workspace_dir.resolve())).replace("\\", "/")


def mkdir(workspace_dir: Path, relative_path: str) -> str:
    target = resolve_safe_path(workspace_dir, relative_path)
    if target.exists() and not target.is_dir():
        raise WorkspaceFileError("A file already exists at that path")
    target.mkdir(parents=True, exist_ok=True)
    return str(target.relative_to(workspace_dir.resolve())).replace("\\", "/")


def delete_path(workspace_dir: Path, relative_path: str) -> None:
    target = resolve_safe_path(workspace_dir, relative_path)
    root = workspace_dir.resolve()
    if target == root:
        raise WorkspaceFileError("Cannot delete workspace root")
    if not target.exists():
        raise WorkspaceFileError(f"Path not found: {relative_path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def rename_path(workspace_dir: Path, from_path: str, to_path: str) -> str:
    source = resolve_safe_path(workspace_dir, from_path)
    dest = resolve_safe_path(workspace_dir, to_path)
    if not source.exists():
        raise WorkspaceFileError(f"Path not found: {from_path}")
    if dest.exists():
        raise WorkspaceFileError(f"Destination already exists: {to_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source.rename(dest)
    return str(dest.relative_to(workspace_dir.resolve())).replace("\\", "/")


def format_content(path: str, content: str) -> str:
    """Format JSON / YAML / TF vars-ish content; otherwise normalize newlines."""
    lower = path.lower()
    text = content.replace("\r\n", "\n")
    if lower.endswith((".json",)):
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
    if lower.endswith((".yaml", ".yml")):
        documents = list(yaml.safe_load_all(text))
        if not documents:
            return text if text.endswith("\n") else text + "\n"
        dumped = "\n---\n".join(
            yaml.safe_dump(
                doc,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            ).rstrip()
            for doc in documents
            if doc is not None
        )
        return dumped + "\n"
    # HCL / TS / other - ensure trailing newline
    return text if text.endswith("\n") else text + "\n"

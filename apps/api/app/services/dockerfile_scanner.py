"""Scan GitHub repositories for Dockerfiles and stack markers."""

from __future__ import annotations

import base64
from typing import Any

from github import GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository

from app.core.logging import get_logger
from app.schemas.dockerfile_schema import DetectedDockerfile, ProjectStack
from app.services.dockerfile_scaffold import detect_stack
from app.services.github_app import get_installation_client

logger = get_logger(__name__)

_DOCKERFILE_NAMES = frozenset(
    {
        "dockerfile",
        "dockerfile.dev",
        "dockerfile.prod",
        "dockerfile.production",
        "containerfile",
    }
)
_MAX_DEPTH = 4
_MAX_ENTRIES = 400
_MAX_FILE_BYTES = 200_000


class DockerfileScannerError(RuntimeError):
    """Failed to scan repository contents."""


def scan_repository_dockerfiles(
    *,
    installation_id: int,
    full_name: str,
    ref: str | None = None,
) -> tuple[list[DetectedDockerfile], ProjectStack, list[str], str]:
    """Return dockerfiles, detected stack, root markers, and resolved ref."""
    client, resolved_installation_id, _, _ = get_installation_client(
        installation_id=installation_id,
    )
    try:
        repo = client.get_repo(full_name)
    except GithubException as exc:
        raise DockerfileScannerError(
            f"Unable to open repository {full_name}: {_github_message(exc)}"
        ) from exc

    resolved_ref = (ref or repo.default_branch or "main").strip()
    root_paths: list[str] = []
    dockerfiles: list[DetectedDockerfile] = []
    visited = 0

    try:
        _walk(
            repo,
            path="",
            ref=resolved_ref,
            depth=0,
            root_paths=root_paths,
            dockerfiles=dockerfiles,
            visited=visited,
        )
    except GithubException as exc:
        raise DockerfileScannerError(
            f"Failed to list repository contents: {_github_message(exc)}"
        ) from exc

    stack, markers = detect_stack(root_paths)
    logger.info(
        "dockerfile_scan_complete",
        full_name=full_name,
        ref=resolved_ref,
        dockerfile_count=len(dockerfiles),
        stack=stack.value,
        installation_id=resolved_installation_id,
    )
    return dockerfiles, stack, markers, resolved_ref


def list_root_file_names(
    *,
    installation_id: int,
    full_name: str,
    ref: str | None = None,
) -> tuple[list[str], str]:
    """Lightweight root listing for scaffold stack detection."""
    client, _, _, _ = get_installation_client(installation_id=installation_id)
    try:
        repo = client.get_repo(full_name)
    except GithubException as exc:
        raise DockerfileScannerError(
            f"Unable to open repository {full_name}: {_github_message(exc)}"
        ) from exc

    resolved_ref = (ref or repo.default_branch or "main").strip()
    try:
        contents = repo.get_contents("", ref=resolved_ref)
    except GithubException as exc:
        if exc.status == 404:
            return [], resolved_ref
        raise DockerfileScannerError(_github_message(exc)) from exc

    if not isinstance(contents, list):
        contents = [contents]

    names: list[str] = []
    for item in contents:
        if getattr(item, "type", None) == "file":
            names.append(item.path)
        elif getattr(item, "type", None) == "dir":
            names.append(f"{item.path}/")
    return names, resolved_ref


def _walk(
    repo: Repository,
    *,
    path: str,
    ref: str,
    depth: int,
    root_paths: list[str],
    dockerfiles: list[DetectedDockerfile],
    visited: int,
) -> int:
    if depth > _MAX_DEPTH or visited >= _MAX_ENTRIES:
        return visited

    try:
        contents = repo.get_contents(path or "", ref=ref)
    except GithubException as exc:
        if exc.status == 404:
            return visited
        raise

    if not isinstance(contents, list):
        contents = [contents]

    for item in contents:
        visited += 1
        if visited > _MAX_ENTRIES:
            break

        item_path: str = item.path
        item_type: str = getattr(item, "type", "") or ""

        if depth == 0:
            root_paths.append(item_path)

        if item_type == "dir":
            # Skip heavy / irrelevant directories.
            base = item_path.rsplit("/", 1)[-1].lower()
            if base in {
                "node_modules",
                ".git",
                "vendor",
                "dist",
                "build",
                ".next",
                "target",
                "venv",
                ".venv",
                "__pycache__",
            }:
                continue
            visited = _walk(
                repo,
                path=item_path,
                ref=ref,
                depth=depth + 1,
                root_paths=root_paths,
                dockerfiles=dockerfiles,
                visited=visited,
            )
            continue

        if item_type != "file":
            continue

        if not _is_dockerfile_path(item_path):
            continue

        content = _decode_content(item)
        if content is None:
            continue
        dockerfiles.append(
            DetectedDockerfile(
                path=item_path,
                content=content,
                size_bytes=len(content.encode("utf-8")),
                sha=getattr(item, "sha", None),
            )
        )

    return visited


def _is_dockerfile_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    lower = name.lower()
    if lower in _DOCKERFILE_NAMES:
        return True
    if lower.startswith("dockerfile.") or lower.startswith("containerfile."):
        return True
    return False


def _decode_content(item: ContentFile | Any) -> str | None:
    encoding = getattr(item, "encoding", None)
    raw = getattr(item, "content", None)
    size = int(getattr(item, "size", 0) or 0)
    if size > _MAX_FILE_BYTES:
        logger.warning("dockerfile_skip_oversized", path=getattr(item, "path", None), size=size)
        return None
    if encoding == "base64" and isinstance(raw, str):
        try:
            return base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if isinstance(raw, str) and raw:
        return raw
    # Large files may need a dedicated download; try get_contents path again via decoded_content.
    decoded = getattr(item, "decoded_content", None)
    if isinstance(decoded, (bytes, bytearray)):
        try:
            return decoded.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _github_message(exc: GithubException) -> str:
    if isinstance(exc.data, dict):
        message = exc.data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return str(exc)

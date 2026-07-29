from __future__ import annotations

import re
from urllib.parse import urlparse


_SSH_GIT_RE = re.compile(
    r"^git@(?P<host>[^:]+):(?P<path>.+?)(?:\.git)?$",
    re.IGNORECASE,
)


def normalize_git_repo_full_name(value: str) -> str | None:
    """Normalize clone/HTML/SSH URLs and bare `owner/repo` to lowercase `owner/repo`."""
    cleaned = value.strip()
    if not cleaned:
        return None

    ssh_match = _SSH_GIT_RE.match(cleaned)
    if ssh_match:
        path = ssh_match.group("path").strip("/")
        return _as_owner_repo(path)

    lower = cleaned.lower()
    if lower.startswith("ssh://"):
        parsed = urlparse(cleaned)
        path = parsed.path.lstrip("/")
        if path.startswith("git@"):
            return normalize_git_repo_full_name(path)
        return _as_owner_repo(path.removesuffix(".git"))

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        return _as_owner_repo(parsed.path.lstrip("/").removesuffix(".git"))

    return _as_owner_repo(cleaned.removesuffix(".git"))


def _as_owner_repo(path: str) -> str | None:
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}".lower()


def branch_from_git_ref(ref: str) -> str | None:
    """Extract branch name from `refs/heads/<branch>` (returns None for tags/other)."""
    prefix = "refs/heads/"
    if not ref.startswith(prefix):
        return None
    branch = ref[len(prefix) :].strip()
    return branch or None


def short_commit_sha(full_sha: str, *, length: int = 7) -> str:
    cleaned = full_sha.strip()
    if not cleaned or set(cleaned) <= {"0"}:
        return ""
    return cleaned[:length]

from __future__ import annotations

import re
from urllib.parse import urlparse


_SSH_GIT_RE = re.compile(
    r"^git@(?P<host>[^:]+):(?P<path>.+?)(?:\.git)?$",
    re.IGNORECASE,
)

# Control-plane placeholder used when a workspace has no upstream clone URL.
# Remote VMs cannot resolve this host; deploy must sync the workspace over SSH.
_LAUNCHPAD_WORKSPACE_HOSTS = frozenset(
    {
        "launchpad.local",
        "localhost",
        "127.0.0.1",
        "::1",
    }
)


def is_launchpad_workspace_git_url(value: str) -> bool:
    """True for synthetic workspace URLs that are not cloneable from a remote VM."""
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    lower = cleaned.lower()
    if "launchpad.local/workspaces/" in lower:
        return True
    try:
        parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host in _LAUNCHPAD_WORKSPACE_HOSTS and "/workspaces/" in (parsed.path or "").lower():
        return True
    return False


def is_remote_cloneable_git_url(value: str) -> bool:
    """True when the URL can be cloned on a cloud VM (real GitHub/GitLab/etc.).

    Link-repo is the default app source for cloud running-instance deploys.
    Placeholder ``launchpad.local`` URLs and empty values are not cloneable.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    if is_launchpad_workspace_git_url(cleaned):
        return False
    if cleaned.startswith("git@"):
        return True
    try:
        parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in _LAUNCHPAD_WORKSPACE_HOSTS:
        return False
    scheme = (parsed.scheme or "https").lower()
    return scheme in {"http", "https", "ssh", "git"} or cleaned.startswith("ssh://")


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

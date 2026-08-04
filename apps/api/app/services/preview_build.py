"""Clone git repos and build preview container images (Docker + kind load)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, sanitize_log_message
from app.schemas.k8s import DeployMode
from app.services.git_urls import normalize_git_repo_full_name, short_commit_sha

logger = get_logger(__name__)

_LAUNCHPAD_LOCAL_HOST = "launchpad.local"
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


@dataclass(frozen=True, slots=True)
class PreviewBuildResult:
    image: str
    commit_sha: str
    simulated: bool


class PreviewBuildError(RuntimeError):
    """Preview image build failed."""


def preview_build_eligible(
    *,
    settings: Settings,
    git_repo_url: str,
    template_id: str | None,
    deploy_mode: str,
    workload_image_override: bool = False,
) -> bool:
    """Return True when the worker should build from source instead of a fixed image."""
    if not settings.preview_build_enabled:
        return False
    if template_id is not None:
        return False
    if deploy_mode == DeployMode.MANIFEST.value:
        return False
    if workload_image_override:
        return False
    if _LAUNCHPAD_LOCAL_HOST in git_repo_url.lower():
        return False
    return normalize_git_repo_full_name(git_repo_url) is not None


def build_image_ref(
    *,
    settings: Settings,
    environment_id: str,
    commit_sha: str,
) -> str:
    tag = short_commit_sha(commit_sha) or "latest"
    env_slug = environment_id.replace("-", "")[:12]
    if settings.preview_image_registry:
        registry = settings.preview_image_registry.rstrip("/")
        return f"{registry}/{env_slug}:{tag}"
    prefix = settings.preview_build_image_prefix.strip("/") or "launchpad-preview"
    return f"{prefix}/{env_slug}:{tag}"


def _github_https_clone_url(repo_url: str, token: str | None) -> str:
    full_name = normalize_git_repo_full_name(repo_url)
    if full_name is None:
        raise PreviewBuildError(f"Unsupported git repository URL: {repo_url}")
    if token:
        safe_token = quote(token, safe="")
        return f"https://x-access-token:{safe_token}@github.com/{full_name}.git"
    return f"https://github.com/{full_name}.git"


def _resolve_clone_url(repo_url: str, token: str | None) -> str:
    parsed = urlparse(repo_url.strip())
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if host in _GITHUB_HOSTS or "github.com" in repo_url.lower():
        return _github_https_clone_url(repo_url, token)
    if token and parsed.scheme in {"http", "https"}:
        userinfo = f"x-access-token:{quote(token, safe='')}@"
        return f"{parsed.scheme}://{userinfo}{parsed.netloc}{parsed.path}"
    return repo_url.strip()


def _git_binary() -> str:
    git = shutil.which("git")
    if git is None:
        raise PreviewBuildError("git is not installed - required for preview builds")
    return git


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _resolve_build_token(settings: Settings) -> str | None:
    if settings.github_pat:
        return settings.github_pat.strip() or None
    try:
        from app.services.github_app import (
            GitHubAppAuthError,
            get_installation_access_token,
            is_github_app_configured,
        )

        if not is_github_app_configured(settings):
            return None
        return get_installation_access_token(settings=settings)
    except Exception as exc:
        from app.services.github_app import GitHubAppAuthError

        if isinstance(exc, GitHubAppAuthError):
            logger.warning("preview_build_github_token_unavailable", error=str(exc))
            return None
        logger.warning("preview_build_github_token_unavailable", error=str(exc))
        return None


def clone_git_repository(
    *,
    repo_url: str,
    branch: str,
    commit_sha: str | None,
    token: str | None,
    dest: Path,
) -> str:
    """Clone a git repository into ``dest`` and return the resolved HEAD sha."""
    return _clone_repository(
        repo_url=repo_url,
        branch=branch,
        commit_sha=commit_sha,
        token=token,
        dest=dest,
    )


def _clone_repository(
    *,
    repo_url: str,
    branch: str,
    commit_sha: str | None,
    token: str | None,
    dest: Path,
) -> str:
    git = _git_binary()
    clone_url = _resolve_clone_url(repo_url, token)
    branch_clean = branch.strip()
    if not branch_clean:
        raise PreviewBuildError("git_branch is required for preview builds")

    clone_cmd = [
        git,
        "clone",
        "--depth",
        "1",
        "--branch",
        branch_clean,
        clone_url,
        str(dest),
    ]
    proc = subprocess.run(
        clone_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = sanitize_log_message((proc.stderr or proc.stdout or "git clone failed").strip())
        raise PreviewBuildError(f"git clone failed: {detail[:800]}")

    resolved_sha = (
        subprocess.check_output([git, "rev-parse", "HEAD"], cwd=dest, text=True).strip()
    )
    target_sha = (commit_sha or "").strip()
    if target_sha and not resolved_sha.startswith(target_sha):
        fetch_proc = subprocess.run(
            [git, "fetch", "--depth", "1", "origin", target_sha],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
        )
        if fetch_proc.returncode != 0:
            detail = sanitize_log_message(
                (fetch_proc.stderr or fetch_proc.stdout or "git fetch failed").strip()
            )
            raise PreviewBuildError(f"git fetch commit failed: {detail[:800]}")
        checkout_proc = subprocess.run(
            [git, "checkout", "--detach", "FETCH_HEAD"],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
        )
        if checkout_proc.returncode != 0:
            detail = sanitize_log_message(
                (checkout_proc.stderr or checkout_proc.stdout or "git checkout failed").strip()
            )
            raise PreviewBuildError(f"git checkout failed: {detail[:800]}")
        resolved_sha = (
            subprocess.check_output([git, "rev-parse", "HEAD"], cwd=dest, text=True).strip()
        )
    return resolved_sha


def _docker_build(*, context: Path, dockerfile: str, tag: str) -> None:
    import docker

    client = docker.from_env()
    dockerfile_path = context / dockerfile
    if not dockerfile_path.is_file():
        raise PreviewBuildError(
            f"{dockerfile} not found at repository root - add a Dockerfile to enable preview builds"
        )
    try:
        _, build_logs = client.images.build(
            path=str(context),
            tag=tag,
            dockerfile=dockerfile,
            rm=True,
            pull=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.info("preview_build_docker", line=sanitize_log_message(line)[:500])
    except docker.errors.BuildError as exc:
        detail = sanitize_log_message(str(exc))
        raise PreviewBuildError(f"docker build failed: {detail[:800]}") from exc
    except Exception as exc:
        raise PreviewBuildError(f"docker build failed: {sanitize_log_message(str(exc))[:800]}") from exc


def _kind_load_image(*, tag: str, cluster_name: str) -> None:
    from app.services.manifest_deploy import _load_image_to_local_cluster

    success = _load_image_to_local_cluster(tag, cluster_name=cluster_name)
    if not success:
        logger.warning("preview_build_local_image_load_warn", tag=tag, cluster=cluster_name)


def _registry_push(*, tag: str) -> None:
    import docker

    client = docker.from_env()
    try:
        push_logs = client.images.push(tag, stream=True, decode=True)
        for chunk in push_logs:
            status = chunk.get("status") or chunk.get("error")
            if status:
                logger.info("preview_build_push", status=sanitize_log_message(str(status))[:300])
            if chunk.get("error"):
                raise PreviewBuildError(str(chunk["error"]))
    except PreviewBuildError:
        raise
    except Exception as exc:
        raise PreviewBuildError(f"registry push failed: {sanitize_log_message(str(exc))[:800]}") from exc


def build_preview_image_sync(
    *,
    settings: Settings | None = None,
    environment_id: str,
    git_repo_url: str,
    git_branch: str,
    commit_sha: str | None = None,
) -> PreviewBuildResult:
    """Blocking clone + docker build + optional kind load / registry push."""
    cfg = settings or get_settings()
    token = _resolve_build_token(cfg)
    tag = build_image_ref(
        settings=cfg,
        environment_id=environment_id,
        commit_sha=commit_sha or git_branch,
    )

    if not _docker_available():
        if not cfg.kubernetes_enabled:
            simulated_sha = short_commit_sha(commit_sha or "") or "simulated"
            logger.info(
                "preview_build_simulated",
                environment_id=environment_id,
                image=tag,
            )
            return PreviewBuildResult(image=tag, commit_sha=simulated_sha, simulated=True)
        raise PreviewBuildError(
            "Docker is not available - start Docker Desktop or disable PREVIEW_BUILD_ENABLED"
        )

    with tempfile.TemporaryDirectory(prefix="launchpad-preview-") as tmp:
        context = Path(tmp) / "src"
        context.mkdir()
        resolved_sha = _clone_repository(
            repo_url=git_repo_url,
            branch=git_branch,
            commit_sha=commit_sha,
            token=token,
            dest=context,
        )
        _docker_build(
            context=context,
            dockerfile=cfg.preview_build_dockerfile,
            tag=tag,
        )

    if cfg.preview_image_registry:
        _registry_push(tag=tag)
    elif cfg.preview_build_kind_load and cfg.kubernetes_enabled:
        _kind_load_image(tag=tag, cluster_name=cfg.kind_cluster_name)

    return PreviewBuildResult(
        image=tag,
        commit_sha=short_commit_sha(resolved_sha) or resolved_sha[:7],
        simulated=False,
    )


async def build_preview_image(
    *,
    settings: Settings | None = None,
    environment_id: str,
    git_repo_url: str,
    git_branch: str,
    commit_sha: str | None = None,
) -> PreviewBuildResult:
    cfg = settings or get_settings()
    timeout = max(30.0, cfg.preview_build_timeout_seconds)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                build_preview_image_sync,
                settings=cfg,
                environment_id=environment_id,
                git_repo_url=git_repo_url,
                git_branch=git_branch,
                commit_sha=commit_sha,
            ),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise PreviewBuildError(
            f"Preview build timed out after {int(timeout)}s"
        ) from exc

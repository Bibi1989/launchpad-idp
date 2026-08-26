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
# Generated Dockerfiles listen on this port so the app matches the manifest's probe.
_PLATFORM_LISTEN_PORT = 8080


def _dockerfile_looks_unsafe_for_nginx_daemonization(text: str) -> bool:
    """Best-effort detection: nginx that daemonizes will cause the pod to exit.

    Many nginx images start nginx in the foreground only when
    ``nginx -g 'daemon off;'`` is used. When a Dockerfile starts nginx without
    forcing foreground mode, the container's main process exits immediately and
    Kubernetes reports the pod as ``Completed``.
    """
    raw = text or ""
    low = raw.lower()

    # If we already force foreground, keep the user's Dockerfile.
    if "daemon off" in low or "daemon-off" in low:
        return False

    if "nginx" not in low:
        return False

    # Only override for nginx-based images / entrypoints.
    nginx_origin = any(
        marker in low
        for marker in (
            "from nginx",
            "from nginxinc/nginx-unprivileged",
            "/docker-entrypoint.sh",
            "/docker-entrypoint.d/",
        )
    )
    if not nginx_origin:
        return False

    # If CMD/ENTRYPOINT invokes nginx without forcing foreground mode, nginx
    # will daemonize and the container main process will exit.
    for line in raw.splitlines():
        line_low = (line or "").strip().lower()
        if not line_low.startswith(("cmd", "entrypoint", "command")):
            continue
        # If this line calls nginx but does not force foreground, consider it unsafe.
        if "nginx" in line_low and "daemon off" not in line_low and "daemon-off" not in line_low:
            return True

    # Also handle Dockerfiles that use a shell entrypoint like:
    #   CMD nginx
    for line in raw.splitlines():
        line_low = (line or "").strip().lower()
        if "service nginx" in line_low and "start" in line_low:
            return True

        if line_low.startswith("cmd") and "nginx" in line_low and "daemon off" not in line_low:
            return True

    return False


def _ensure_dockerfile(
    context: Path, *, dockerfile_rel: str = "Dockerfile", force: bool = False
) -> str | None:
    """Generate a best-effort Dockerfile from the detected stack when the repo lacks one.

    Returns the detected stack value when a Dockerfile was generated, else None (a real
    Dockerfile already exists). Raises PreviewBuildError when no Dockerfile exists and the
    stack cannot be detected. When ``force`` is True, a fresh Launchpad Dockerfile is
    generated even if the repo ships one (overwriting it in the throwaway clone) - the
    recovery path for a repo whose own Dockerfile fails to build.
    """
    target = context / dockerfile_rel
    if target.is_file() and not force:
        # Some linked repos ship an nginx Dockerfile that daemonizes (no
        # ``daemon off``), which causes the pod to exit immediately after startup.
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError:
            return None
        if _dockerfile_looks_unsafe_for_nginx_daemonization(raw):
            logger.info(
                "preview_build_dockerfile_override_nginx_foreground",
                file=str(target),
            )
        else:
            return None

    from app.schemas.dockerfile_schema import ProjectStack
    from app.services.dockerfile_scaffold import detect_stack, scaffold_dockerfile

    framework = ""
    try:
        from pkg.detector import ProjectDetectorEngine

        detection = ProjectDetectorEngine().detect(context)
        services = [s for s in detection.services if getattr(s, "enabled", True)]
        primary = next(
            (s for s in services if getattr(s, "is_preview_target", False)), None
        ) or (services[0] if services else None)
        if primary is not None:
            framework = str(getattr(primary, "framework", "") or "").strip().lower()
    except Exception as exc:  # noqa: BLE001 - detector is best-effort
        logger.info("preview_build_detector_unavailable", error=str(exc)[:200])

    try:
        stack = ProjectStack(framework)
    except ValueError:
        stack, _ = detect_stack([p.name for p in context.iterdir() if p.is_file()])

    if stack in (ProjectStack.UNKNOWN, ProjectStack.GENERIC):
        if force and target.is_file():
            logger.info("preview_build_dockerfile_regenerate_skipped_unknown_stack")
            return None
        raise PreviewBuildError(
            "No Dockerfile in the linked repository and its stack could not be detected "
            "to generate one. Add a Dockerfile at the repository root and re-provision."
        )

    content = scaffold_dockerfile(stack, app_name="app", listen_port=_PLATFORM_LISTEN_PORT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info(
        "preview_build_dockerfile_autogenerated",
        stack=stack.value,
        listen_port=_PLATFORM_LISTEN_PORT,
        regenerated=force,
    )
    return stack.value


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
    if deploy_mode in {
        DeployMode.MANIFEST.value,
        DeployMode.COMPOSE.value,
        DeployMode.ATTACH.value,
    }:
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
    from app.services.github_app import resolve_git_clone_token

    return resolve_git_clone_token(settings=settings)


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


def _docker_build(
    *,
    context: Path,
    dockerfile: str,
    tag: str,
    platform: str | None = None,
    build_env: dict[str, str] | None = None,
) -> None:
    import docker

    from app.core.config import get_settings
    from app.services.env_blueprint import docker_build_args_from_env

    client = docker.from_env()
    dockerfile_path = context / dockerfile
    if not dockerfile_path.is_file():
        raise PreviewBuildError(
            f"{dockerfile} not found at repository root - add a Dockerfile to enable preview builds"
        )
    pull_base = bool(get_settings().preview_build_pull_base)
    merged_env = collect_context_build_env(context)
    if build_env:
        from app.services.env_blueprint import merge_env_layers

        merged_env = merge_env_layers(merged_env, build_env)
    buildargs: dict[str, str] = {}
    flat = docker_build_args_from_env(merged_env)
    for i in range(0, len(flat) - 1, 2):
        if flat[i] == "--build-arg" and "=" in flat[i + 1]:
            key, value = flat[i + 1].split("=", 1)
            buildargs[key] = value
    try:
        build_kwargs: dict[str, object] = {
            "path": str(context),
            "tag": tag,
            "dockerfile": dockerfile,
            "rm": True,
            "pull": pull_base,
        }
        if platform:
            build_kwargs["platform"] = platform
        if buildargs:
            build_kwargs["buildargs"] = buildargs
        _, build_logs = client.images.build(**build_kwargs)
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
    regenerate_dockerfile: bool = False,
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
        # Generate a Dockerfile from the detected stack when the repo lacks one (or when
        # regeneration is forced). Keeps otherwise-unbuildable linked repos deployable.
        generated = _ensure_dockerfile(
            context,
            dockerfile_rel=cfg.preview_build_dockerfile,
            force=regenerate_dockerfile,
        )
        platform = "linux/amd64" if cfg.preview_image_registry else None
        try:
            _docker_build(
                context=context,
                dockerfile=cfg.preview_build_dockerfile,
                tag=tag,
                platform=platform,
            )
        except PreviewBuildError as exc:
            # Self-heal: the repo's OWN Dockerfile failed to build (commonly a COPY whose
            # source is not in the build context). Fall back to a generated Dockerfile and
            # build once more. Only when the repo supplied the Dockerfile and we have not
            # already forced regeneration - otherwise surface the real error.
            if generated is not None or regenerate_dockerfile:
                raise
            logger.warning(
                "preview_build_repo_dockerfile_failed_regenerating", error=str(exc)[:300]
            )
            regen = _ensure_dockerfile(
                context, dockerfile_rel=cfg.preview_build_dockerfile, force=True
            )
            if regen is None:
                raise
            _docker_build(
                context=context,
                dockerfile=cfg.preview_build_dockerfile,
                tag=tag,
                platform=platform,
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
    regenerate_dockerfile: bool = False,
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
                regenerate_dockerfile=regenerate_dockerfile,
            ),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise PreviewBuildError(
            f"Preview build timed out after {int(timeout)}s"
        ) from exc


def _linked_repo_slug(repo: dict) -> str:
    """Derive a stable service slug for a linked repo (launch- stripped, sanitized)."""
    import re

    full = str(repo.get("full_name") or "").strip()
    url = str(repo.get("git_repo_url") or "").strip()
    name = ""
    if full and "/" in full:
        name = full.rsplit("/", 1)[-1]
    if not name:
        norm = normalize_git_repo_full_name(url)
        if norm and "/" in norm:
            name = norm.rsplit("/", 1)[-1]
    if not name and url:
        name = url.rstrip("/").rsplit("/", 1)[-1]
    name = name.removesuffix(".git").strip().lower().removeprefix("launch-")
    return re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "app"


_FRONTEND_HINTS = ("frontend", "web", "ui", "client", "site", "app")
_BACKEND_HINTS = ("backend", "server", "api", "service", "worker")


def _kind_hint(text: str) -> str:
    low = text.lower()
    if any(h in low for h in _BACKEND_HINTS):
        return "backend"
    if any(h in low for h in _FRONTEND_HINTS):
        return "frontend"
    return ""


def _read_build_plan_contexts(workspace_root: Path) -> list[tuple[str, str]]:
    """Return (image_name, context_rel) entries from ``.launchpad/image-builds.json``."""
    import json

    plan_path = workspace_root / ".launchpad" / "image-builds.json"
    if not plan_path.is_file():
        return []
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("image"):
                entries.append(
                    (str(entry["image"]).strip(), str(entry.get("context") or ".").strip() or ".")
                )
    return entries


_LAUNCHPAD_BUILD_ENV_LOCAL = ".env.production.local"

# Keys that must never be missing at Vite/Next/Nuxt build time (undefined in JS).
_FRONTEND_BUILD_API_KEYS: tuple[str, ...] = (
    "API_URL",
    "BACKEND_URL",
    "VITE_API_URL",
    "VITE_API_BASE_URL",
    "VUE_APP_API_URL",
    "PUBLIC_API_URL",
    "NG_APP_API_URL",
    "NEXT_PUBLIC_API_URL",
    "NEXT_PUBLIC_API_BASE_URL",
    "NUXT_PUBLIC_API_URL",
    "NUXT_PUBLIC_API_BASE",
)


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    from pkg.detector.env_example import parse_env_example_text

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {
        item.key: (item.suggested_value or item.example_value or "")
        for item in parse_env_example_text(text, source=path.name)
    }


def collect_context_build_env(context: Path) -> dict[str, str]:
    """Read build-time env from a service context (blueprint + Launchpad local override)."""
    from app.services.env_blueprint import merge_env_layers, load_service_env_blueprint

    root = Path(context)
    if not root.is_dir():
        return {}
    return merge_env_layers(
        load_service_env_blueprint(root),
        _parse_dotenv_file(root / ".env.production"),
        _parse_dotenv_file(root / ".env"),
        _parse_dotenv_file(root / _LAUNCHPAD_BUILD_ENV_LOCAL),
    )


def _write_build_env_file(app_dir: Path, build_env: dict[str, str]) -> None:
    """Force-write Launchpad build-time env into every Vite/Next dotenv filename.

    Always overwrites Launchpad-managed files so empty committed ``VITE_API_URL=``
    cannot leave ``import.meta.env.VITE_API_URL`` as JS ``undefined``.
    """
    if not build_env:
        return
    lines = [
        "# Written by Launchpad for preview builds (do not commit).",
        "# Forces SPA public API base so the browser never sees /undefined/...",
        "",
    ]
    for key in sorted(build_env):
        value = build_env[key]
        cleaned_key = str(key or "").strip()
        if not cleaned_key or value is None:
            continue
        lines.append(f"{cleaned_key}={value}")
    payload = "\n".join(lines) + "\n"
    for filename in (
        _LAUNCHPAD_BUILD_ENV_LOCAL,
        ".env.production",
        ".env.local",
        ".env",
    ):
        target = app_dir / filename
        try:
            target.write_text(payload, encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            logger.warning("build_env_write_failed", path=str(target), error=str(exc)[:200])


def ensure_dockerfile_bakes_frontend_api(dockerfile: Path) -> bool:
    """Inject ARG/ENV for public API keys before the frontend build RUN.

    Repo Dockerfiles often omit ``ARG VITE_API_URL``, so ``docker build --build-arg``
    is ignored and Vite still compiles ``undefined``. Returns True when patched.
    """
    if not dockerfile.is_file():
        return False
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError:
        return False
    if "LAUNCHPAD_FRONTEND_API_BAKE" in text:
        return False
    build_markers = (
        "npm run build",
        "pnpm run build",
        "yarn build",
        "vite build",
        "nuxt build",
        "next build",
        "ng build",
    )
    if not any(marker in text for marker in build_markers):
        return False
    arg_lines = ["# LAUNCHPAD_FRONTEND_API_BAKE"]
    for key in _FRONTEND_BUILD_API_KEYS:
        arg_lines.append(f"ARG {key}=/api")
    for key in _FRONTEND_BUILD_API_KEYS:
        arg_lines.append(f"ENV {key}=${key}")
    inject = "\n".join(arg_lines) + "\n"
    # Insert immediately before the first build RUN that contains a marker.
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    for line in lines:
        lowered = line.lower()
        if not inserted and line.lstrip().upper().startswith("RUN") and any(
            marker in lowered for marker in build_markers
        ):
            out.append(inject)
            inserted = True
        out.append(line)
    if not inserted:
        # Fall back: append before last stage if no RUN build matched.
        out = [*lines[:-1], inject, lines[-1]] if lines else [inject]
        inserted = True
    try:
        dockerfile.write_text("".join(out), encoding="utf-8")
    except OSError:
        return False
    logger.info("dockerfile_frontend_api_bake_injected", dockerfile=str(dockerfile))
    return True


def force_frontend_api_build_context(
    context: Path,
    *,
    api_base: str = "/api",
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write dotenv files + patch Dockerfile so SPA builds always bake ``api_base``."""
    from app.services.env_blueprint import apply_blueprints_to_build_env, merge_env_layers
    from app.services.manifest_deploy import same_origin_frontend_api_env

    root = Path(context)
    forced = merge_env_layers(
        same_origin_frontend_api_env(api_base),
        extra_env,
    )
    merged = apply_blueprints_to_build_env(root, forced)
    # Platform /api must win over blueprint placeholders that leave keys empty.
    merged = merge_env_layers(merged, forced)
    _write_build_env_file(root, merged)
    df = root / "Dockerfile"
    if df.is_file():
        ensure_dockerfile_bakes_frontend_api(df)
    return merged


def materialize_linked_repos_to_apps(
    *,
    workspace_root: Path,
    linked_repos: list[dict],
    settings: Settings | None = None,
    regenerate_dockerfile: bool = False,
    build_env: dict[str, str] | None = None,
) -> list[str]:
    """Clone linked repos onto disk so build-based deploys build the ACTUAL repositories.

    Linked repos are URL references, not files on disk. This clones each into the build
    context the workspace expects so the existing image-build pipeline (Compose repair and
    MANIFEST ``plan_workspace_image_builds`` / ``build_and_load_kind_images``) builds the
    exact image tags the manifests reference.

    Placement: when ``.launchpad/image-builds.json`` exists, each linked repo is cloned into
    a plan context (matched frontend/backend by name, else in order). Otherwise it falls back
    to ``apps/<slug>/``. A Dockerfile is ensured (generated from the detected stack if the
    repo ships none). Returns the list of destination directory names materialized.
    """
    cfg = settings or get_settings()
    repos = [
        r
        for r in (linked_repos or [])
        if isinstance(r, dict)
        and str(r.get("git_repo_url") or "").strip()
        and _LAUNCHPAD_LOCAL_HOST not in str(r.get("git_repo_url") or "").lower()
    ]
    if not repos:
        return []

    token = _resolve_build_token(cfg)

    # Resolve destinations. Prefer the workspace's own build-plan contexts so the built
    # tags match the manifest image references exactly (no name guessing).
    plan = _read_build_plan_contexts(workspace_root)
    destinations: list[tuple[Path, dict]] = []
    if plan:
        remaining = list(repos)
        for image_name, ctx_rel in plan:
            if not remaining:
                break
            want = _kind_hint(image_name)
            match = next((r for r in remaining if _kind_hint(_linked_repo_slug(r)) == want), None) if want else None
            chosen = match or remaining[0]
            remaining.remove(chosen)
            ctx = workspace_root if ctx_rel in {".", ""} else workspace_root / ctx_rel
            destinations.append((ctx, chosen))
    else:
        apps_dir = workspace_root / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        for repo in repos:
            destinations.append((apps_dir / _linked_repo_slug(repo), repo))

    def _materialize_one(dest: Path, repo: dict) -> str | None:
        """Clone one repo + ensure a Dockerfile. Returns the dir name, or None to skip.

        Raises PreviewBuildError on a clone failure (a real repo that cannot be fetched
        should fail the provision), but only skips when the stack is undetectable.
        """
        url = str(repo["git_repo_url"]).strip()
        branch = str(repo.get("git_branch") or "main").strip() or "main"
        # Fresh clone each provision so the deployed stack tracks the latest commit.
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        try:
            clone_git_repository(
                repo_url=url, branch=branch, commit_sha=None, token=token, dest=dest
            )
        except PreviewBuildError as exc:
            logger.warning("linked_repo_materialize_clone_failed", dest=str(dest), error=str(exc)[:300])
            shutil.rmtree(dest, ignore_errors=True)
            raise
        shutil.rmtree(dest / ".git", ignore_errors=True)
        try:
            _ensure_dockerfile(dest, dockerfile_rel="Dockerfile", force=regenerate_dockerfile)
        except PreviewBuildError as exc:
            logger.warning("linked_repo_materialize_no_dockerfile", dest=str(dest), error=str(exc)[:300])
            shutil.rmtree(dest, ignore_errors=True)
            return None
        # Bake build-time connection vars (backend URL under framework keys) into the
        # context so statically-built frontends pick them up at npm build.
        from app.services.env_blueprint import merge_env_layers

        slug = _linked_repo_slug(repo)
        hint = _kind_hint(slug) or _kind_hint(dest.name)
        is_frontend = hint == "frontend" or any(
            token in slug for token in ("frontend", "web-ui", "web", "client", "ui")
        )
        if is_frontend:
            force_frontend_api_build_context(dest, api_base="/api", extra_env=build_env)
        else:
            from app.services.env_blueprint import apply_blueprints_to_build_env

            _write_build_env_file(dest, apply_blueprints_to_build_env(dest, build_env or {}))
        logger.info("linked_repo_materialized", dest=str(dest), branch=str(repo.get("git_branch") or "main"))
        return dest.name

    # Clone repos concurrently - they are independent network operations, so serial
    # clones dominate provision time for multi-repo workspaces. A clone failure
    # propagates (fails provision); an undetectable stack is skipped.
    if len(destinations) <= 1:
        return [name for (dest, repo) in destinations if (name := _materialize_one(dest, repo))]

    from concurrent.futures import ThreadPoolExecutor

    materialized: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, len(destinations))) as pool:
        futures = [pool.submit(_materialize_one, dest, repo) for dest, repo in destinations]
        for future in futures:
            name = future.result()  # re-raises PreviewBuildError from a failed clone
            if name:
                materialized.append(name)
    return materialized

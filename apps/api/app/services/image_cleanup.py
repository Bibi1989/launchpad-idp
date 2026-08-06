"""Best-effort removal of locally built Docker images on preview/workspace destroy."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.preview_build import build_image_ref

logger = get_logger(__name__)

# Official/shared images that must never be removed on teardown (they are reused
# across previews and would just be re-pulled). Only locally-built app images go.
IMAGE_REMOVE_DENYLIST = frozenset(
    {
        "nginx",
        "postgres",
        "mysql",
        "mariadb",
        "mongo",
        "redis",
        "busybox",
        "alpine",
        "http-echo",
    }
)


def is_removable_app_image(image: str, *, default_image: str = "") -> bool:
    """Return True when ``image`` is safe to delete (not a shared base image)."""
    tag = (image or "").strip()
    if not tag or tag.endswith(":<none>") or tag == "<none>:<none>":
        return False
    if default_image and tag == default_image:
        return False
    repo = tag.rsplit(":", 1)[0].rsplit("/", 1)[-1].lower()
    return repo not in IMAGE_REMOVE_DENYLIST


def resolve_local_cluster_short_name(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    raw = (cfg.resolved_kubernetes_context or cfg.kubernetes_context or cfg.kind_cluster_name or "launchpad")
    return raw.removeprefix("kind-").removeprefix("k3d-")


def _node_container_name(cluster_name: str, settings: Settings) -> str:
    if settings.local_k8s_engine == "k3s":
        return f"k3d-{cluster_name}-server-0"
    return f"{cluster_name}-control-plane"


def list_host_images_for_reference(reference: str) -> list[str]:
    """List host Docker tags matching a repository reference filter."""
    ref = (reference or "").strip()
    if not ref or not shutil.which("docker"):
        return []
    # Accept either ``repo`` or ``repo:tag``; expand bare repo to ``repo:*``.
    filt = ref if ":" in ref.split("/")[-1] else f"{ref}:*"
    try:
        proc = subprocess.run(
            [
                "docker",
                "images",
                "--filter",
                f"reference={filt}",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("docker_images_list_failed", reference=filt, error=str(exc))
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines():
        tag = line.strip()
        if tag and not tag.endswith(":<none>"):
            out.append(tag)
    return out


def collect_preview_environment_images(
    *,
    settings: Settings,
    environment_id: str,
    workload_image: str | None,
    commit_sha: str | None = None,
) -> list[str]:
    """Collect host tags tied to a preview environment (workload + built preview tags)."""
    images: list[str] = []
    if workload_image:
        images.append(workload_image.strip())
    if commit_sha:
        images.append(
            build_image_ref(
                settings=settings,
                environment_id=environment_id,
                commit_sha=commit_sha,
            )
        )
    env_slug = environment_id.replace("-", "")[:12]
    if settings.preview_image_registry:
        repo = f"{settings.preview_image_registry.rstrip('/')}/{env_slug}"
    else:
        prefix = settings.preview_build_image_prefix.strip("/") or "launchpad-preview"
        repo = f"{prefix}/{env_slug}"
    images.extend(list_host_images_for_reference(repo))
    # Dedupe while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for image in images:
        if image and image not in seen:
            seen.add(image)
            unique.append(image)
    return unique


def collect_workspace_destroy_images(
    workspace_root: Path | str | None,
    *,
    workload_images: list[str] | None = None,
) -> list[str]:
    """Collect tags to delete when a workspace is destroyed."""
    from app.services.manifest_deploy import collect_workspace_image_tags

    images: list[str] = []
    if workspace_root:
        root = Path(workspace_root)
        if root.is_dir():
            images.extend(collect_workspace_image_tags(root))
    for image in workload_images or []:
        if image:
            images.append(image.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for image in images:
        if image and image not in seen:
            seen.add(image)
            unique.append(image)
    return unique


def remove_local_docker_images(
    images: list[str] | None,
    *,
    cluster_name: str | None = None,
    settings: Settings | None = None,
    remove_from_cluster: bool = True,
) -> list[str]:
    """Best-effort removal from kind/k3d containerd and the host Docker daemon.

    Shared base images are skipped. Failures are swallowed so destroy/teardown
    never fails because an image is already gone.
    """
    if not images or not shutil.which("docker"):
        return []

    cfg = settings or get_settings()
    default_image = cfg.default_workload_image
    cluster = (cluster_name or resolve_local_cluster_short_name(cfg)).strip() or "launchpad"
    node = _node_container_name(cluster, cfg)

    removed: list[str] = []
    seen: set[str] = set()
    for image in images:
        tag = (image or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        if not is_removable_app_image(tag, default_image=default_image):
            continue

        if remove_from_cluster:
            try:
                subprocess.run(
                    ["docker", "exec", node, "crictl", "rmi", tag],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("kind_node_image_remove_failed", image=tag, error=str(exc))

        try:
            proc = subprocess.run(
                ["docker", "rmi", "-f", tag],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0:
                removed.append(tag)
            else:
                detail = (proc.stderr or proc.stdout or "").strip()[-200:]
                logger.info("host_image_remove_skipped", image=tag, detail=detail)
        except Exception as exc:  # noqa: BLE001
            logger.info("host_image_remove_failed", image=tag, error=str(exc))

    if removed:
        logger.info("docker_images_removed", cluster=cluster, images=removed)
    return removed

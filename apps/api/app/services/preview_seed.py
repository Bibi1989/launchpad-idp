"""Discover and apply preview seed.sql / seed.sh after ephemeral Postgres is ready."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.logging import get_logger

logger = get_logger(__name__)

SeedKind = Literal["sql", "shell"]
SeedStatus = Literal["applied", "skipped", "failed", "none"]

_SQL_CANDIDATES = (
    "seed.sql",
    "scripts/seed.sql",
    "db/seed.sql",
    "database/seed.sql",
)
_SHELL_CANDIDATES = (
    "seed.sh",
    "scripts/seed.sh",
    "db/seed.sh",
)

_MAX_SEED_BYTES = 512_000
_JOB_TIMEOUT_SECONDS = 120.0
_SEED_SQL_IMAGE = "postgres:16-alpine"
_SEED_SHELL_IMAGE = "postgres:16-alpine"


@dataclass(frozen=True)
class SeedArtifact:
    kind: SeedKind
    relative_path: str
    absolute_path: Path


@dataclass(frozen=True)
class SeedPlan:
    artifacts: tuple[SeedArtifact, ...]

    @property
    def empty(self) -> bool:
        return not self.artifacts


@dataclass(frozen=True)
class SeedResult:
    status: SeedStatus
    message: str
    applied: tuple[str, ...] = ()


def discover_seed_artifacts(workspace_root: Path) -> SeedPlan:
    """Find seed.sql / seed.sh under common repo paths (sql before shell)."""
    root = workspace_root.resolve()
    found: list[SeedArtifact] = []
    seen: set[str] = set()

    for rel in _SQL_CANDIDATES:
        artifact = _resolve_artifact(root, rel, "sql")
        if artifact is not None and artifact.relative_path not in seen:
            found.append(artifact)
            seen.add(artifact.relative_path)
            break

    for rel in _SHELL_CANDIDATES:
        artifact = _resolve_artifact(root, rel, "shell")
        if artifact is not None and artifact.relative_path not in seen:
            found.append(artifact)
            seen.add(artifact.relative_path)
            break

    return SeedPlan(artifacts=tuple(found))


def _resolve_artifact(root: Path, relative: str, kind: SeedKind) -> SeedArtifact | None:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size <= 0 or size > _MAX_SEED_BYTES:
        logger.info(
            "preview_seed_skipped_size",
            path=str(path),
            size=size,
            max_bytes=_MAX_SEED_BYTES,
        )
        return None
    return SeedArtifact(kind=kind, relative_path=relative, absolute_path=path)


def run_seed_plan(
    provisioner: object,
    *,
    namespace: str,
    plan: SeedPlan,
    enable_postgres: bool,
    kubernetes_enabled: bool,
) -> SeedResult:
    """Apply discovered seeds via a one-shot Job in the preview namespace."""
    if plan.empty:
        return SeedResult(status="none", message="No seed.sql or seed.sh found")

    if not kubernetes_enabled:
        names = ", ".join(a.relative_path for a in plan.artifacts)
        return SeedResult(
            status="skipped",
            message=f"Kubernetes disabled; would apply: {names}",
            applied=tuple(a.relative_path for a in plan.artifacts),
        )

    apply_fn = getattr(provisioner, "run_preview_seed_job", None)
    if not callable(apply_fn):
        return SeedResult(
            status="failed",
            message="Provisioner cannot run preview seed jobs",
        )

    applied: list[str] = []
    for artifact in plan.artifacts:
        if artifact.kind == "sql" and not enable_postgres:
            return SeedResult(
                status="skipped",
                message=(
                    f"Skipped {artifact.relative_path}: enable_postgres is false"
                ),
                applied=tuple(applied),
            )
        try:
            content = artifact.absolute_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SeedResult(
                status="failed",
                message=f"Could not read {artifact.relative_path}: {exc}",
                applied=tuple(applied),
            )
        try:
            apply_fn(
                namespace=namespace,
                kind=artifact.kind,
                relative_path=artifact.relative_path,
                content=content,
                timeout_seconds=_JOB_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "preview_seed_job_failed",
                namespace=namespace,
                path=artifact.relative_path,
                error=str(exc),
            )
            return SeedResult(
                status="failed",
                message=f"Seed {artifact.relative_path} failed: {exc}",
                applied=tuple(applied),
            )
        applied.append(artifact.relative_path)

    return SeedResult(
        status="applied",
        message=f"Applied seed: {', '.join(applied)}",
        applied=tuple(applied),
    )


def seed_job_manifests(
    *,
    namespace: str,
    kind: SeedKind,
    relative_path: str,
    content: str,
    job_name: str,
    config_map_name: str,
) -> list[dict[str, object]]:
    """Build ConfigMap + Job dicts for utils.create_from_dict."""
    filename = "seed.sql" if kind == "sql" else "seed.sh"
    if kind == "sql":
        command = [
            "sh",
            "-c",
            (
                'if [ -z "${DATABASE_URL:-}" ]; then '
                'echo "DATABASE_URL missing from app-secrets" >&2; exit 1; fi; '
                'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f /seed/seed.sql'
            ),
        ]
        image = _SEED_SQL_IMAGE
    else:
        command = ["sh", "/seed/seed.sh"]
        image = _SEED_SHELL_IMAGE

    config_map: dict[str, object] = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": config_map_name,
            "namespace": namespace,
            "labels": {
                "app": "launchpad-seed",
                "launchpad.io/managed-by": "launchpad-idp",
            },
        },
        "data": {filename: content},
    }
    job: dict[str, object] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app": "launchpad-seed",
                "launchpad.io/managed-by": "launchpad-idp",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 300,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "launchpad-seed",
                        "launchpad.io/managed-by": "launchpad-idp",
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 999,
                        "runAsGroup": 999,
                        "fsGroup": 999,
                    },
                    "containers": [
                        {
                            "name": "seed",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": command,
                            "envFrom": [
                                {"secretRef": {"name": "app-secrets"}},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "seed",
                                    "mountPath": "/seed",
                                    "readOnly": True,
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "runAsUser": 999,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        },
                    ],
                    "volumes": [
                        {
                            "name": "seed",
                            "configMap": {
                                "name": config_map_name,
                                "defaultMode": 0o555 if kind == "shell" else 0o444,
                            },
                        },
                    ],
                },
            },
        },
    }
    # relative_path kept for logging callers; unused in manifest body
    _ = relative_path
    return [config_map, job]


def wait_for_job_complete(
    batch_api: object,
    *,
    namespace: str,
    job_name: str,
    timeout_seconds: float = _JOB_TIMEOUT_SECONDS,
) -> None:
    """Block until Job succeeds or raise on failure/timeout."""
    from kubernetes.client.rest import ApiException

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            job = batch_api.read_namespaced_job_status(job_name, namespace)  # type: ignore[attr-defined]
        except ApiException as exc:
            if exc.status == 404:
                time.sleep(1.0)
                continue
            raise
        status = getattr(job, "status", None)
        succeeded = int(getattr(status, "succeeded", 0) or 0)
        failed = int(getattr(status, "failed", 0) or 0)
        if succeeded >= 1:
            return
        if failed >= 1:
            raise RuntimeError(f"Seed job {job_name} failed")
        time.sleep(1.5)
    raise TimeoutError(f"Seed job {job_name} timed out after {timeout_seconds:.0f}s")

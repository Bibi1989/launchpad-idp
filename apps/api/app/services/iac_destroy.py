"""Destroy the cloud infrastructure a workspace applied (terraform / pulumi).

When a workspace is deleted, its generated IaC (and local state) is removed from
disk. Without first running ``destroy``, any real cloud resources the workspace
applied - VPCs, GKE/EKS clusters, Cloud SQL, buckets, load balancers - are
orphaned and keep billing. This runs the engine's destroy in the workspace's
infra directory, authenticated with the workspace's stored cloud credentials,
BEFORE the directory is deleted.

Gated on state existence: a workspace that only rendered IaC (never applied) has
nothing to destroy, so it is skipped quickly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import credentials_to_env
from app.schemas.cloud import CloudCredentials

logger = get_logger(__name__)

# engine value -> CLI binary.
_TF_ENGINES = {"terraform": "terraform", "opentofu": "tofu"}

DestroyStatus = str  # "destroyed" | "skipped" | "failed"


@dataclass(frozen=True, slots=True)
class IaCDestroyResult:
    status: DestroyStatus
    detail: str = ""
    output: str = ""

    @property
    def ok(self) -> bool:
        """True when there is nothing left orphaned (destroyed or nothing to do)."""
        return self.status in {"destroyed", "skipped"}


def _has_terraform_state(tf_dir: Path) -> bool:
    """True when terraform has applied resources worth destroying."""
    for state in tf_dir.glob("*.tfstate"):
        try:
            if state.stat().st_size > 0:
                return True
        except OSError:
            continue
    # Remote backend: state lives elsewhere, but ``.terraform`` exists after init/apply.
    return (tf_dir / ".terraform").is_dir()


def _destroy_env(
    credentials: CloudCredentials | None,
    *,
    org_id: str,
    workspace_id: str,
) -> dict[str, str]:
    """Process env + materialized cloud credentials (keyless OIDC when configured)."""
    env = dict(os.environ)
    if credentials is not None:
        env.update(
            credentials_to_env(
                credentials,
                org_id=org_id,
                workspace_id=workspace_id,
                env_type="production",
            )
        )
    # Non-interactive by default; never prompt on a background destroy.
    env.setdefault("TF_IN_AUTOMATION", "1")
    env.setdefault("PULUMI_SKIP_UPDATE_CHECK", "true")
    return env


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_workspace_iac_destroy(
    *,
    root_dir: str,
    engine: str,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    settings: Settings | None = None,
) -> IaCDestroyResult:
    """Run ``destroy`` for a workspace's applied cloud infra. Never raises."""
    settings = settings or get_settings()
    timeout = float(settings.iac_destroy_timeout_seconds)
    root = Path(root_dir)

    try:
        if engine in _TF_ENGINES:
            return _destroy_terraform(
                cli=_TF_ENGINES[engine],
                tf_dir=root / "infra" / "terraform",
                credentials=credentials,
                org_id=org_id,
                workspace_id=workspace_id,
                timeout=timeout,
            )
        if engine == "pulumi":
            return _destroy_pulumi(
                pulumi_dir=root / "infra" / "pulumi",
                credentials=credentials,
                org_id=org_id,
                workspace_id=workspace_id,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        logger.error("iac_destroy_timeout", engine=engine, workspace_id=workspace_id)
        return IaCDestroyResult("failed", f"{engine} destroy timed out after {timeout:.0f}s")
    except Exception as exc:
        logger.exception("iac_destroy_error", engine=engine, workspace_id=workspace_id)
        return IaCDestroyResult("failed", f"{engine} destroy error: {exc}")

    return IaCDestroyResult("skipped", f"destroy not supported for engine '{engine}'")


def _destroy_terraform(
    *,
    cli: str,
    tf_dir: Path,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    timeout: float,
) -> IaCDestroyResult:
    if not tf_dir.is_dir():
        return IaCDestroyResult("skipped", "no infra/terraform directory")
    if not _has_terraform_state(tf_dir):
        return IaCDestroyResult("skipped", "no terraform state - nothing was applied")
    if shutil.which(cli) is None:
        return IaCDestroyResult("skipped", f"{cli} CLI not installed")

    env = _destroy_env(credentials, org_id=org_id, workspace_id=workspace_id)
    # Re-init so providers/backend are available, then destroy.
    init = _run([cli, "init", "-input=false", "-no-color"], cwd=tf_dir, env=env, timeout=timeout)
    if init.returncode != 0:
        return IaCDestroyResult(
            "failed", f"{cli} init failed", (init.stderr or init.stdout)[-2000:]
        )
    destroy = _run(
        [cli, "destroy", "-auto-approve", "-input=false", "-no-color"],
        cwd=tf_dir,
        env=env,
        timeout=timeout,
    )
    if destroy.returncode != 0:
        return IaCDestroyResult(
            "failed", f"{cli} destroy failed", (destroy.stderr or destroy.stdout)[-2000:]
        )
    logger.info("iac_workspace_cloud_destroyed", cli=cli, workspace_id=workspace_id)
    return IaCDestroyResult("destroyed", f"{cli} destroy complete", destroy.stdout[-2000:])


def _destroy_pulumi(
    *,
    pulumi_dir: Path,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    timeout: float,
) -> IaCDestroyResult:
    if not pulumi_dir.is_dir():
        return IaCDestroyResult("skipped", "no infra/pulumi directory")
    if shutil.which("pulumi") is None:
        return IaCDestroyResult("skipped", "pulumi CLI not installed")

    env = _destroy_env(credentials, org_id=org_id, workspace_id=workspace_id)
    destroy = _run(
        ["pulumi", "destroy", "--yes", "--skip-preview", "--non-interactive"],
        cwd=pulumi_dir,
        env=env,
        timeout=timeout,
    )
    if destroy.returncode != 0:
        stderr = (destroy.stderr or destroy.stdout)
        # No stack selected / nothing initialized => nothing to destroy.
        if "no stack" in stderr.lower() or "no current stack" in stderr.lower():
            return IaCDestroyResult("skipped", "no pulumi stack - nothing was applied")
        return IaCDestroyResult("failed", "pulumi destroy failed", stderr[-2000:])
    logger.info("iac_workspace_cloud_destroyed", cli="pulumi", workspace_id=workspace_id)
    return IaCDestroyResult("destroyed", "pulumi destroy complete", destroy.stdout[-2000:])

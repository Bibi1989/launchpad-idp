"""Preserve and restore Terraform/Pulumi local state across IaC regenerate.

``IaCGenerator.regenerate`` rewrites HCL under ``infra/``. Without stashing
state first, a retry provision loses ``terraform.tfstate`` while GCP/AWS
resources still exist, so the next apply attempts create and hits 409 Conflict.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

_TF_STATE_FILES = (
    "terraform.tfstate",
    "terraform.tfstate.backup",
    ".terraform.lock.hcl",
)


def stash_iac_runtime_state(infra_dir: Path) -> Path | None:
    """Copy terraform/pulumi local state out of ``infra/`` before it is wiped.

    Returns a temporary directory path to pass to ``restore_iac_runtime_state``,
    or None when nothing needed preserving.
    """
    if not infra_dir.is_dir():
        return None

    stash = Path(tempfile.mkdtemp(prefix="launchpad-iac-state-"))
    preserved = 0

    tf_dir = infra_dir / "terraform"
    if tf_dir.is_dir():
        dest_tf = stash / "terraform"
        dest_tf.mkdir(parents=True, exist_ok=True)
        for name in _TF_STATE_FILES:
            src = tf_dir / name
            if src.is_file():
                shutil.copy2(src, dest_tf / name)
                preserved += 1
        state_d = tf_dir / "terraform.tfstate.d"
        if state_d.is_dir():
            shutil.copytree(state_d, dest_tf / "terraform.tfstate.d")
            preserved += 1

    pulumi_dir = infra_dir / "pulumi"
    if pulumi_dir.is_dir():
        for pattern in (".pulumi", "Pulumi.dev.yaml"):
            src = pulumi_dir / pattern
            if src.is_dir():
                shutil.copytree(src, stash / "pulumi" / pattern)
                preserved += 1
            elif src.is_file():
                dest = stash / "pulumi"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / pattern)
                preserved += 1

    if preserved == 0:
        shutil.rmtree(stash, ignore_errors=True)
        return None

    logger.info(
        "iac_runtime_state_stashed",
        infra=str(infra_dir),
        preserved=preserved,
    )
    return stash


def restore_iac_runtime_state(infra_dir: Path, stash_dir: Path | None) -> None:
    """Copy stashed state back after HCL regenerate. Always cleans the stash dir."""
    if stash_dir is None:
        return
    try:
        tf_stash = stash_dir / "terraform"
        if tf_stash.is_dir():
            dest_tf = infra_dir / "terraform"
            dest_tf.mkdir(parents=True, exist_ok=True)
            for item in tf_stash.iterdir():
                target = dest_tf / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)

        pulumi_stash = stash_dir / "pulumi"
        if pulumi_stash.is_dir():
            dest_pulumi = infra_dir / "pulumi"
            dest_pulumi.mkdir(parents=True, exist_ok=True)
            for item in pulumi_stash.iterdir():
                target = dest_pulumi / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)

        logger.info("iac_runtime_state_restored", infra=str(infra_dir))
    finally:
        shutil.rmtree(stash_dir, ignore_errors=True)


def terraform_name_prefix(environment_id: str, *, max_len: int = 55) -> str:
    """Mirror terraform ``local.name_55`` / ``name_63`` for import IDs."""
    raw = (environment_id or "env").lower()
    hyphen = re.sub(r"[^a-z0-9]+", "-", raw)
    collapsed = re.sub(r"-+", "-", hyphen).strip("-") or "env"
    limit = max(8, max_len)
    prefix = f"lp-{collapsed}"[:limit].rstrip("-")
    return prefix or "lp-env"


def is_already_exists_apply_error(output: str) -> bool:
    text = (output or "").lower()
    markers = (
        "already exists",
        "409",
        "conflict",
        "entity already exists",
        "resource already exists",
        "already_exists",
    )
    return any(m in text for m in markers)

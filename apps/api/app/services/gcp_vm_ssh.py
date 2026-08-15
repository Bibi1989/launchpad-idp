"""Ensure GCP VMs accept Launchpad preview SSH keys.

OS Login (``enable-oslogin=TRUE``) ignores instance ``ssh-keys`` metadata, which
is why scaffold SSH fallback hits ``Permission denied (publickey)`` even when
Terraform applied a key. We disable OS Login and publish the preview key via
gcloud after apply, and patch stale generated HCL that still enables OS Login.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.cloud import CloudCredentials, CloudProvider
from app.services.cloud_instance_compute import _credential_env, _gcp_zone

logger = get_logger(__name__)

_OSLOGIN_TRUE_RE = re.compile(
    r'enable-oslogin\s*=\s*"TRUE"',
    re.IGNORECASE,
)


def patch_workspace_disable_os_login(workspace_root: Path) -> int:
    """Rewrite enable-oslogin TRUE → FALSE under terraform/pulumi. Returns files changed."""
    changed = 0
    roots = (
        workspace_root / "infra" / "terraform",
        workspace_root / "infra" / "pulumi",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in (*root.rglob("*.tf"), *root.rglob("*.ts"), *root.rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            patched = _OSLOGIN_TRUE_RE.sub('enable-oslogin = "FALSE"', text)
            patched = re.sub(
                r'(["\']enable-oslogin["\']\s*:\s*)["\']TRUE["\']',
                r'\1"FALSE"',
                patched,
                flags=re.IGNORECASE,
            )
            if patched != text:
                path.write_text(patched, encoding="utf-8")
                changed += 1
                logger.info("gcp_oslogin_hcl_patched", path=str(path))
    return changed


def ensure_gcp_instance_ssh_metadata(
    *,
    instance_name: str,
    zone: str,
    public_key_line: str,
    environment_id: str,
    credentials: CloudCredentials | None,
    ssh_user: str = "ubuntu",
    project_id: str | None = None,
) -> None:
    """Disable OS Login and publish ``ssh-keys`` on a running GCE instance."""
    name = (instance_name or "").strip()
    z = _gcp_zone((zone or "").strip())
    key = (public_key_line or "").strip()
    user = (ssh_user or "ubuntu").strip() or "ubuntu"
    if not name or not z or not key:
        raise ValueError("instance_name, zone, and public_key_line are required")
    if shutil.which("gcloud") is None:
        raise RuntimeError("gcloud CLI is required to publish GCP SSH metadata")

    env = _credential_env(
        credentials,
        environment_id=environment_id,
        provider=CloudProvider.GCP.value,
    )
    # gcloud allows only one --metadata flag; put ssh-keys in a file so commas/spaces
    # in the key line cannot break KEY=VALUE,[KEY=VALUE,...] parsing.
    keys_file: str | None = None
    result: subprocess.CompletedProcess[str] | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".ssh-keys",
            delete=False,
        ) as handle:
            handle.write(f"{user}:{key}\n")
            keys_file = handle.name

        cmd = [
            "gcloud",
            "compute",
            "instances",
            "add-metadata",
            name,
            f"--zone={z}",
            "--metadata=enable-oslogin=FALSE",
            f"--metadata-from-file=ssh-keys={keys_file}",
            "--quiet",
        ]
        if project_id:
            cmd.append(f"--project={project_id}")

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        if keys_file:
            Path(keys_file).unlink(missing_ok=True)

    if result is None or result.returncode != 0:
        detail = ""
        if result is not None:
            detail = (result.stderr or result.stdout or "").strip()[-800:]
        raise RuntimeError(f"gcloud add-metadata failed: {detail or 'no result'}")

    logger.info(
        "gcp_instance_ssh_metadata_updated",
        instance=name,
        zone=z,
        environment_id=environment_id,
    )
    # Guest agent needs a moment to rewrite authorized_keys after OS Login flips.
    time.sleep(3.0)

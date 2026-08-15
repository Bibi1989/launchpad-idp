"""Apply workspace Terraform / OpenTofu / Pulumi and parse outputs.

Symmetric to ``iac_destroy``: the scaffold under ``infra/`` is the source of truth
for cloud create. The control plane only runs the CLI and reads outputs.

On retry, if cloud resources already exist (409) but local state was lost,
we import known addresses into state and re-apply so Terraform updates instead
of creating.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import credentials_to_env
from app.schemas.cloud import CloudCredentials
from app.services.iac_state import is_already_exists_apply_error, terraform_name_prefix

logger = get_logger(__name__)

_TF_ENGINES = {"terraform": "terraform", "opentofu": "tofu"}

ApplyStatus = str  # "applied" | "skipped" | "failed"


@dataclass(frozen=True, slots=True)
class IaCApplyResult:
    status: ApplyStatus
    detail: str = ""
    output: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"applied", "skipped"}


def _apply_env(
    credentials: CloudCredentials | None,
    *,
    org_id: str,
    workspace_id: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
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
    env.setdefault("TF_IN_AUTOMATION", "1")
    env.setdefault("PULUMI_SKIP_UPDATE_CHECK", "true")
    if extra:
        env.update(extra)
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_workspace_iac_apply(
    *,
    root_dir: str,
    engine: str,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    settings: Settings | None = None,
    tf_vars: dict[str, str] | None = None,
) -> IaCApplyResult:
    """Run ``apply`` for a workspace's cloud infra. Never raises."""
    settings = settings or get_settings()
    timeout = float(
        getattr(settings, "iac_apply_timeout_seconds", None)
        or settings.iac_destroy_timeout_seconds
    )
    root = Path(root_dir)

    try:
        if engine in _TF_ENGINES:
            return _apply_terraform(
                cli=_TF_ENGINES[engine],
                tf_dir=root / "infra" / "terraform",
                credentials=credentials,
                org_id=org_id,
                workspace_id=workspace_id,
                timeout=timeout,
                tf_vars=tf_vars,
            )
        if engine == "pulumi":
            return _apply_pulumi(
                pulumi_dir=root / "infra" / "pulumi",
                credentials=credentials,
                org_id=org_id,
                workspace_id=workspace_id,
                timeout=timeout,
                tf_vars=tf_vars,
            )
    except subprocess.TimeoutExpired:
        logger.error("iac_apply_timeout", engine=engine, workspace_id=workspace_id)
        return IaCApplyResult("failed", f"{engine} apply timed out after {timeout:.0f}s")
    except Exception as exc:
        logger.exception("iac_apply_error", engine=engine, workspace_id=workspace_id)
        return IaCApplyResult("failed", f"{engine} apply error: {exc}")

    return IaCApplyResult("skipped", f"apply not supported for engine '{engine}'")


def parse_preview_fields(outputs: dict[str, Any]) -> dict[str, str | None]:
    """Map terraform/pulumi outputs to preview host / URL fields."""
    from app.services.cloud_instance_compute import parse_gcp_compute_instance_id

    def _get(*keys: str) -> str | None:
        for key in keys:
            raw = outputs.get(key)
            if raw is None:
                continue
            if isinstance(raw, dict) and "value" in raw:
                raw = raw["value"]
            text = str(raw).strip()
            if text and text.lower() not in {"null", "none"}:
                return text
        return None

    public_ip = _get("public_ip", "instance_public_ip", "vm_public_ip", "ec2_public_ip")
    preview_url = _get(
        "preview_url",
        "cloud_run_url",
        "app_runner_service_url",
        "container_app_url",
    )
    if preview_url and not preview_url.startswith("http"):
        preview_url = f"https://{preview_url}"
    instance_id = _get(
        "instance_id",
        "ec2_instance_id",
        "compute_instance_id",
        "vm_instance_id",
    )
    listen_port = _get("app_listen_port", "listen_port")
    instance_zone: str | None = None
    instance_name: str | None = None
    parsed = parse_gcp_compute_instance_id(instance_id)
    if parsed is not None:
        _project, instance_zone, instance_name = parsed
    if not preview_url and public_ip:
        port = listen_port or "8080"
        preview_url = f"http://{public_ip}:{port}"
    return {
        "public_ip": public_ip,
        "preview_url": preview_url,
        "instance_id": instance_id,
        "instance_zone": instance_zone,
        "instance_name": instance_name,
        "listen_port": listen_port,
    }


def _normalize_tf_outputs(raw: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and "value" in value:
            flat[key] = value["value"]
        else:
            flat[key] = value
    return flat


def _read_tfvars_map(tf_dir: Path) -> dict[str, str]:
    path = tf_dir / "terraform.tfvars"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"\s*$', line)
        if match:
            out[match.group(1)] = match.group(2)
            continue
        match_num = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(\d+)\s*$", line)
        if match_num:
            out[match_num.group(1)] = match_num.group(2)
    return out


def _read_tf_variable_defaults(tf_dir: Path) -> dict[str, str]:
    """Parse string/number defaults from root ``variables.tf``.

    Launchpad puts ``project_id`` / ``region`` in variable defaults, not
    ``terraform.tfvars``. Import must use those values or it targets the wrong zone.
    """
    path = tf_dir / "variables.tf"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for match in re.finditer(
        r'variable\s+"([^"]+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{]*)*)\}',
        text,
        re.DOTALL,
    ):
        name, body = match.group(1), match.group(2)
        dm = re.search(r'default\s*=\s*"([^"]*)"', body)
        if dm:
            out[name] = dm.group(1)
            continue
        dm_num = re.search(r"default\s*=\s*(\d+)", body)
        if dm_num:
            out[name] = dm_num.group(1)
    return out


def _resolve_tf_context(
    tf_dir: Path,
    env: dict[str, str],
    tf_vars: dict[str, str] | None,
) -> dict[str, str]:
    """Merge tfvars, CLI -var overrides, and variables.tf defaults."""
    merged = {
        **_read_tf_variable_defaults(tf_dir),
        **_read_tfvars_map(tf_dir),
        **(tf_vars or {}),
    }
    project = (
        merged.get("project_id")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or env.get("GCP_PROJECT")
        or env.get("CLOUDSDK_CORE_PROJECT")
        or ""
    ).strip()
    region = (merged.get("region") or "us-central1").strip()
    env_id = (merged.get("environment_id") or "env").strip()
    return {
        "project_id": project,
        "region": region,
        "environment_id": env_id,
    }


def _var_args(tf_vars: dict[str, str] | None) -> list[str]:
    return [f"-var={key}={value}" for key, value in (tf_vars or {}).items()]


_WITH_ADDRESS_RE = re.compile(
    r"\bwith\s+((?:module\.[a-zA-Z0-9_.-]+\.)?[a-zA-Z0-9_]+\.[a-zA-Z0-9_-]+)\s*,",
    re.IGNORECASE,
)
_GCP_PROJECTS_ID_RE = re.compile(r"'(projects/[^']+)'")


def _parse_already_exists_from_apply_error(output: str) -> list[tuple[str, str]]:
    """Extract (terraform address, GCP id) pairs from apply 409 errors."""
    found: list[tuple[str, str]] = []
    # Split on Error: blocks so each conflict keeps its own with/id.
    blocks = re.split(r"(?=\nError:|\AError:)", output or "")
    for block in blocks:
        if "already exist" not in block.lower() and "409" not in block:
            continue
        address_match = _WITH_ADDRESS_RE.search(block)
        if not address_match:
            continue
        address = address_match.group(1).strip()
        id_match = _GCP_PROJECTS_ID_RE.search(block)
        resource_id = id_match.group(1) if id_match else ""
        found.append((address, resource_id))
    return found


def _looks_like_gcp_network_conflict(output: str) -> bool:
    """True when apply failed while creating VPC/subnet that already exists."""
    text = (output or "").lower()
    if "already exist" in text or "409" in text:
        return any(m in text for m in ("subnet", "network", "vpc", "firewall"))
    # Truncated apply logs often end mid-create of subnet after a 409.
    creating_subnet = "google_compute_subnetwork" in text and "creating" in text
    creating_vpc = "google_compute_network" in text and "creating" in text
    return creating_subnet or creating_vpc or "error creating subnetwork" in text


def _known_gcp_import_targets(ctx: dict[str, str]) -> list[tuple[str, str]]:
    project = ctx["project_id"]
    region = ctx["region"]
    env_id = ctx["environment_id"]
    if not project:
        return []
    name_55 = terraform_name_prefix(env_id, max_len=55)
    name_63 = terraform_name_prefix(env_id, max_len=63)
    zone = f"{region}-a"
    return [
        (
            "module.vpc.google_compute_network.vpc",
            f"projects/{project}/global/networks/{name_55}-vpc",
        ),
        (
            "module.vpc.google_compute_subnetwork.subnet",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-subnet",
        ),
        (
            "module.vpc.google_compute_subnetwork.public",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-public",
        ),
        (
            "module.vpc.google_compute_subnetwork.private",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-private",
        ),
        (
            "module.cluster.google_compute_instance.app",
            f"projects/{project}/zones/{zone}/instances/{name_55}-vm",
        ),
        (
            "module.cluster.google_compute_firewall.launchpad_vm",
            f"projects/{project}/global/firewalls/{name_55}-vm-fw",
        ),
        (
            "module.secrets.google_secret_manager_secret.app_secrets",
            f"projects/{project}/secrets/{name_55}-secrets",
        ),
        (
            "google_artifact_registry_repository.ar",
            f"projects/{project}/locations/{region}/repositories/{name_63}",
        ),
    ]


def _merge_import_targets(
    *,
    known: list[tuple[str, str]],
    from_error: list[tuple[str, str]],
    ctx: dict[str, str],
) -> list[tuple[str, str]]:
    """Prefer IDs parsed from the apply error; fill gaps from known scaffold map."""
    by_address: dict[str, str] = {}
    for address, resource_id in known:
        if resource_id:
            by_address[address] = resource_id
    for address, resource_id in from_error:
        if resource_id:
            by_address[address] = resource_id
        elif address not in by_address:
            # AR errors often omit the projects/... id; synthesize from context.
            for known_addr, known_id in known:
                if known_addr == address and known_id:
                    by_address[address] = known_id
                    break
            if address == "google_artifact_registry_repository.ar" and address not in by_address:
                project, region = ctx["project_id"], ctx["region"]
                name_63 = terraform_name_prefix(ctx["environment_id"], max_len=63)
                if project:
                    by_address[address] = (
                        f"projects/{project}/locations/{region}/repositories/{name_63}"
                    )
    return list(by_address.items())


def _import_existing_gcp_resources(
    *,
    cli: str,
    tf_dir: Path,
    env: dict[str, str],
    timeout: float,
    tf_vars: dict[str, str] | None,
    apply_error: str = "",
) -> list[str]:
    """Import known / error-reported GCP addresses when apply hits already-exists."""
    ctx = _resolve_tf_context(tf_dir, env, tf_vars)
    if not ctx["project_id"]:
        logger.warning("iac_import_skipped_no_project", tf_dir=str(tf_dir))
        return []

    targets = _merge_import_targets(
        known=_known_gcp_import_targets(ctx),
        from_error=_parse_already_exists_from_apply_error(apply_error),
        ctx=ctx,
    )
    if not targets:
        return []

    var_flags = _var_args(tf_vars)
    imported: list[str] = []
    for address, resource_id in targets:
        if not resource_id:
            continue
        cmd = [
            cli,
            "import",
            "-input=false",
            "-no-color",
            *var_flags,
            address,
            resource_id,
        ]
        result = _run(cmd, cwd=tf_dir, env=env, timeout=min(timeout, 180.0))
        combined = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
        if (
            result.returncode == 0
            or "already managed" in combined
            or "resource already managed" in combined
        ):
            imported.append(address)
            logger.info(
                "iac_import_ok",
                address=address,
                resource_id=resource_id,
            )
            continue
        if (
            "cannot import" in combined
            or "does not exist" in combined
            or "not found" in combined
        ):
            logger.info(
                "iac_import_skipped_missing",
                address=address,
                resource_id=resource_id,
                detail=(result.stderr or result.stdout or "")[-300:],
            )
            continue
        logger.warning(
            "iac_import_failed",
            address=address,
            resource_id=resource_id,
            detail=(result.stderr or result.stdout or "")[-400:],
        )
    return imported


def _collect_outputs(
    *,
    cli: str,
    tf_dir: Path,
    env: dict[str, str],
    timeout: float,
    workspace_id: str,
) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    out = _run(
        [cli, "output", "-json", "-no-color"],
        cwd=tf_dir,
        env=env,
        timeout=min(timeout, 120.0),
    )
    if out.returncode == 0 and (out.stdout or "").strip():
        try:
            parsed = json.loads(out.stdout)
            if isinstance(parsed, dict):
                outputs = _normalize_tf_outputs(parsed)
        except json.JSONDecodeError:
            logger.warning("iac_apply_output_json_invalid", workspace_id=workspace_id)
    return outputs


def _apply_terraform(
    *,
    cli: str,
    tf_dir: Path,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    timeout: float,
    tf_vars: dict[str, str] | None,
) -> IaCApplyResult:
    if not tf_dir.is_dir():
        return IaCApplyResult("skipped", "no infra/terraform directory")
    if shutil.which(cli) is None:
        return IaCApplyResult("skipped", f"{cli} CLI not installed")

    env = _apply_env(credentials, org_id=org_id, workspace_id=workspace_id)
    init = _run(
        [cli, "init", "-input=false", "-no-color"],
        cwd=tf_dir,
        env=env,
        timeout=timeout,
    )
    if init.returncode != 0:
        return IaCApplyResult(
            "failed",
            f"{cli} init failed",
            (init.stderr or init.stdout)[-2000:],
        )

    apply_cmd = [
        cli,
        "apply",
        "-auto-approve",
        "-input=false",
        "-no-color",
        *_var_args(tf_vars),
    ]
    apply = _run(apply_cmd, cwd=tf_dir, env=env, timeout=timeout)
    if apply.returncode != 0:
        combined = (apply.stderr or "") + "\n" + (apply.stdout or "")
        if is_already_exists_apply_error(combined):
            logger.warning(
                "iac_apply_already_exists_adopting",
                workspace_id=workspace_id,
            )
            last_error = combined
            adopted_any = False
            # Import + re-apply up to a few rounds (new 409s can appear after
            # partial adoption, e.g. secret then AR then VM).
            for attempt in range(1, 4):
                imported = _import_existing_gcp_resources(
                    cli=cli,
                    tf_dir=tf_dir,
                    env=env,
                    timeout=timeout,
                    tf_vars=tf_vars,
                    apply_error=last_error,
                )
                if not imported and not adopted_any:
                    break
                if imported:
                    adopted_any = True
                retry = _run(apply_cmd, cwd=tf_dir, env=env, timeout=timeout)
                if retry.returncode == 0:
                    outputs = _collect_outputs(
                        cli=cli,
                        tf_dir=tf_dir,
                        env=env,
                        timeout=timeout,
                        workspace_id=workspace_id,
                    )
                    return IaCApplyResult(
                        "applied",
                        f"{cli} apply complete after importing existing resources",
                        retry.stdout[-2000:],
                        outputs=outputs,
                    )
                last_error = (retry.stderr or "") + "\n" + (retry.stdout or "")
                if not is_already_exists_apply_error(last_error):
                    # Subnet replace with create_before_destroy (legacy HCL) or
                    # orphan VPC can fail mid-create without a clean 409 parse.
                    if _looks_like_gcp_network_conflict(last_error):
                        logger.warning(
                            "iac_apply_network_conflict_retry_import",
                            workspace_id=workspace_id,
                            attempt=attempt,
                        )
                        continue
                    return IaCApplyResult(
                        "failed",
                        f"{cli} apply failed after import",
                        last_error[-2000:],
                    )
                logger.warning(
                    "iac_apply_still_already_exists",
                    workspace_id=workspace_id,
                    attempt=attempt,
                )
            if adopted_any:
                return IaCApplyResult(
                    "failed",
                    f"{cli} apply failed after import",
                    last_error[-2000:],
                )
        return IaCApplyResult(
            "failed",
            f"{cli} apply failed",
            combined[-2000:],
        )

    outputs = _collect_outputs(
        cli=cli,
        tf_dir=tf_dir,
        env=env,
        timeout=timeout,
        workspace_id=workspace_id,
    )
    logger.info(
        "iac_workspace_cloud_applied",
        cli=cli,
        workspace_id=workspace_id,
        output_keys=sorted(outputs.keys()),
    )
    return IaCApplyResult(
        "applied",
        f"{cli} apply complete",
        apply.stdout[-2000:],
        outputs=outputs,
    )


def _apply_pulumi(
    *,
    pulumi_dir: Path,
    credentials: CloudCredentials | None,
    org_id: str,
    workspace_id: str,
    timeout: float,
    tf_vars: dict[str, str] | None = None,
) -> IaCApplyResult:
    from app.services.iac_cli import (
        IaCCliError,
        ensure_pulumi_env,
        prepare_pulumi_project,
        resolve_pulumi_bin,
    )

    # Prefer infra/pulumi; fall back to workspace-root Pulumi.yaml from older scaffolds.
    resolved = pulumi_dir
    if not resolved.is_dir():
        root_yaml = pulumi_dir.parent.parent / "Pulumi.yaml"
        if root_yaml.is_file():
            resolved = root_yaml.parent
        else:
            return IaCApplyResult("skipped", "no infra/pulumi directory")

    settings = get_settings()
    try:
        pulumi_bin = resolve_pulumi_bin(
            install_if_missing=bool(settings.pulumi_cli_auto_install),
        )
    except IaCCliError as exc:
        return IaCApplyResult("failed", str(exc))

    env = ensure_pulumi_env(
        _apply_env(credentials, org_id=org_id, workspace_id=workspace_id)
    )
    try:
        prepare_pulumi_project(resolved, pulumi_bin=pulumi_bin, timeout=timeout)
    except IaCCliError as exc:
        return IaCApplyResult("failed", str(exc))

    for key, value in (tf_vars or {}).items():
        if value is None:
            continue
        cfg = _run(
            [pulumi_bin, "config", "set", "--plaintext", str(key), str(value)],
            cwd=resolved,
            env=env,
            timeout=min(timeout, 60.0),
        )
        if cfg.returncode != 0:
            logger.warning(
                "iac_apply_pulumi_config_set_failed",
                key=key,
                detail=(cfg.stderr or cfg.stdout or "")[-500:],
                workspace_id=workspace_id,
            )

    # Pin GCP project for the provider (parity with TF_VAR_project_id).
    gcp_project = (
        (tf_vars or {}).get("project_id")
        or env.get("GOOGLE_CLOUD_PROJECT")
        or env.get("TF_VAR_project_id")
        or env.get("GCP_PROJECT_ID")
        or ""
    ).strip()
    if gcp_project:
        env.setdefault("GOOGLE_PROJECT", gcp_project)
        env.setdefault("GOOGLE_CLOUD_PROJECT", gcp_project)
        for key in ("gcp:project", "project_id"):
            cfg = _run(
                [pulumi_bin, "config", "set", "--plaintext", key, gcp_project],
                cwd=resolved,
                env=env,
                timeout=min(timeout, 60.0),
            )
            if cfg.returncode != 0:
                logger.warning(
                    "iac_apply_pulumi_gcp_project_config_failed",
                    key=key,
                    detail=(cfg.stderr or cfg.stdout or "")[-500:],
                    workspace_id=workspace_id,
                )

    apply = _run(
        [pulumi_bin, "up", "--yes", "--skip-preview", "--non-interactive"],
        cwd=resolved,
        env=env,
        timeout=timeout,
    )
    if apply.returncode != 0:
        combined = (apply.stderr or "") + "\n" + (apply.stdout or "")
        if is_already_exists_apply_error(combined):
            logger.warning(
                "iac_apply_pulumi_already_exists_adopting",
                workspace_id=workspace_id,
            )
            refresh = _run(
                [pulumi_bin, "refresh", "--yes", "--non-interactive"],
                cwd=resolved,
                env=env,
                timeout=min(timeout, 600.0),
            )
            if refresh.returncode != 0:
                logger.warning(
                    "iac_apply_pulumi_refresh_failed",
                    detail=(refresh.stderr or refresh.stdout or "")[-500:],
                    workspace_id=workspace_id,
                )
            imported = _import_existing_pulumi_gcp(
                pulumi_bin=pulumi_bin,
                pulumi_dir=resolved,
                env=env,
                timeout=timeout,
                tf_vars=tf_vars,
            )
            retry = _run(
                [pulumi_bin, "up", "--yes", "--skip-preview", "--non-interactive"],
                cwd=resolved,
                env=env,
                timeout=timeout,
            )
            if retry.returncode == 0:
                apply = retry
            else:
                last = (retry.stderr or "") + "\n" + (retry.stdout or "")
                return IaCApplyResult(
                    "failed",
                    "pulumi up failed after import"
                    + (f" (imported={len(imported)})" if imported else ""),
                    last[-2000:],
                )
        else:
            return IaCApplyResult(
                "failed",
                "pulumi up failed",
                combined[-2000:],
            )

    outputs: dict[str, Any] = {}
    out = _run(
        [pulumi_bin, "stack", "output", "--json"],
        cwd=resolved,
        env=env,
        timeout=min(timeout, 120.0),
    )
    if out.returncode == 0 and (out.stdout or "").strip():
        try:
            parsed = json.loads(out.stdout)
            if isinstance(parsed, dict):
                outputs = parsed
        except json.JSONDecodeError:
            logger.warning("iac_apply_pulumi_output_invalid", workspace_id=workspace_id)

    logger.info("iac_workspace_cloud_applied", cli="pulumi", workspace_id=workspace_id)
    return IaCApplyResult(
        "applied",
        "pulumi up complete",
        apply.stdout[-2000:] if apply.stdout else "",
        outputs=outputs,
    )


def _known_pulumi_gcp_import_targets(ctx: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return (type, name, id) triples matching scaffolded Pulumi resource names."""
    project = ctx["project_id"]
    region = ctx["region"]
    env_id = ctx["environment_id"]
    if not project:
        return []
    name_55 = terraform_name_prefix(env_id, max_len=55)
    name_63 = terraform_name_prefix(env_id, max_len=63)
    zone = f"{region}-a"
    return [
        (
            "gcp:compute/network:Network",
            "lp-vpc",
            f"projects/{project}/global/networks/{name_55}-vpc",
        ),
        (
            "gcp:compute/subnetwork:Subnetwork",
            "lp-subnet",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-subnet",
        ),
        (
            "gcp:compute/subnetwork:Subnetwork",
            "lp-subnet-public",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-public",
        ),
        (
            "gcp:compute/subnetwork:Subnetwork",
            "lp-subnet-private",
            f"projects/{project}/regions/{region}/subnetworks/{name_55}-private",
        ),
        (
            "gcp:compute/instance:Instance",
            "lp-vm",
            f"projects/{project}/zones/{zone}/instances/{name_55}-vm",
        ),
        (
            "gcp:compute/firewall:Firewall",
            "lp-vm-fw",
            f"projects/{project}/global/firewalls/{name_55}-vm-fw",
        ),
        (
            "gcp:secretmanager/secret:Secret",
            "lp-secrets",
            f"projects/{project}/secrets/{name_55}-secrets",
        ),
        (
            "gcp:artifactregistry/repository:Repository",
            "lp-ar",
            f"projects/{project}/locations/{region}/repositories/{name_63}",
        ),
    ]


def _import_existing_pulumi_gcp(
    *,
    pulumi_bin: str,
    pulumi_dir: Path,
    env: dict[str, str],
    timeout: float,
    tf_vars: dict[str, str] | None,
) -> list[str]:
    """Best-effort ``pulumi import`` for scaffold GCP resources after 409 conflicts."""
    ctx = _resolve_tf_context(pulumi_dir, env, tf_vars)
    # Prefer Pulumi.yaml sibling defaults when terraform.tfvars is absent.
    if not ctx["project_id"]:
        cfg = _run(
            [pulumi_bin, "config", "get", "gcp:project"],
            cwd=pulumi_dir,
            env=env,
            timeout=30.0,
        )
        if cfg.returncode == 0 and (cfg.stdout or "").strip():
            ctx["project_id"] = cfg.stdout.strip()
    if not ctx["environment_id"]:
        ctx["environment_id"] = pulumi_dir.parent.parent.name or "env"
    if not ctx["project_id"]:
        logger.warning("iac_pulumi_import_skipped_no_project", pulumi_dir=str(pulumi_dir))
        return []

    imported: list[str] = []
    for type_token, name, resource_id in _known_pulumi_gcp_import_targets(ctx):
        cmd = [
            pulumi_bin,
            "import",
            type_token,
            name,
            resource_id,
            "--yes",
            "--non-interactive",
        ]
        result = _run(cmd, cwd=pulumi_dir, env=env, timeout=min(timeout, 180.0))
        combined = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
        if result.returncode == 0 or "already exists" in combined or "already imported" in combined:
            imported.append(name)
            logger.info(
                "iac_pulumi_import_ok",
                name=name,
                resource_id=resource_id,
            )
            continue
        if "does not exist" in combined or "not found" in combined:
            logger.info(
                "iac_pulumi_import_skipped_missing",
                name=name,
                resource_id=resource_id,
            )
            continue
        logger.warning(
            "iac_pulumi_import_failed",
            name=name,
            resource_id=resource_id,
            detail=(result.stderr or result.stdout or "")[-400:],
        )
    return imported

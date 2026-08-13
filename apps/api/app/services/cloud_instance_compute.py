"""Cloud VM provisioning and workspace image build/push for instance previews."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from app.core.logging import get_logger, sanitize_log_message
from app.core.secrets import project_id_from_gcp_sa_json
from app.schemas.cloud import CloudCredentials, CloudProvider, RunningInstanceConfig

logger = get_logger(__name__)

_LOCAL_IMAGE_RE = re.compile(r"^lp-ws-[a-z0-9-]+(?::local)?$", re.IGNORECASE)
_REPO_NAME = "launchpad-previews"


class CloudInstanceComputeError(RuntimeError):
    """Cloud VM or registry operation failed."""


def is_ephemeral_local_image(image: str | None) -> bool:
    raw = (image or "").strip()
    if not raw:
        return True
    if raw.endswith(":local"):
        return True
    if _LOCAL_IMAGE_RE.match(raw.split("/")[-1]):
        return True
    return False


def _run_cmd(
    cmd: list[str],
    *,
    timeout: float,
    check: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    logger.info("cloud_instance_exec", cmd=cmd[:6])
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloudInstanceComputeError(f"Command timed out: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise CloudInstanceComputeError(f"Command failed to start: {exc}") from exc
    if check and completed.returncode != 0:
        detail = sanitize_log_message((completed.stderr or completed.stdout or "failed")[:600])
        raise CloudInstanceComputeError(f"{' '.join(cmd[:3])} failed: {detail}")
    return completed


_GCP_AUTH_ENV_KEYS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
    "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
    "LAUNCHPAD_OIDC_JWT",
)


def _credential_env(
    credentials: CloudCredentials | None,
    *,
    environment_id: str,
) -> dict[str, str]:
    import os
    import tempfile
    from pathlib import Path

    if credentials is None:
        return dict(os.environ)
    from app.core.secrets import (
        credentials_to_env,
        materialize_external_account_credentials,
        project_id_from_gcp_sa_json,
    )

    # Start from the process env, but drop ambient ADC / gcloud auth overrides so a
    # leftover /tmp/launchpad_oidc_token.jwt (or stale WIF config) cannot win over
    # the workspace credentials we are about to materialize.
    merged = {
        key: value
        for key, value in os.environ.items()
        if key not in _GCP_AUTH_ENV_KEYS
    }
    for key, value in credentials_to_env(credentials, workspace_id=environment_id).items():
        if value:
            merged[key] = value

    # Isolate gcloud config so ~/.config/gcloud (limited-scope user login) cannot win.
    gcloud_cfg = Path(tempfile.gettempdir()) / "launchpad-gcloud-config" / _env_slug(environment_id)
    gcloud_cfg.mkdir(parents=True, exist_ok=True)
    merged["CLOUDSDK_CONFIG"] = str(gcloud_cfg)

    gac = (merged.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if gac:
        # gcloud prefers CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE over ADC when set.
        merged["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = gac
        # Keys / WIF ADC win over optional Connect access tokens.
        merged.pop("CLOUDSDK_AUTH_ACCESS_TOKEN", None)
        cfg_path = Path(gac)
        if cfg_path.is_file():
            try:
                payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("type") == "external_account":
                cred_src = payload.get("credential_source")
                token_file = ""
                if isinstance(cred_src, dict):
                    token_file = str(cred_src.get("file") or "").strip()
                if token_file and not Path(token_file).is_file():
                    raise CloudInstanceComputeError(
                        "GCP Workload Identity token file is missing "
                        f"({token_file}). Re-save workspace cloud credentials "
                        "or re-run provision so Launchpad can mint a fresh OIDC JWT."
                    )
            logger.info(
                "gcp_auth_mode",
                mode=str((payload or {}).get("type") or "credential_file"),
                environment_id=environment_id,
            )

    # gcloud/ADC need a key file path, not GCP_SA_KEY inline JSON.
    # External-account blobs are already rewritten by credentials_to_env.
    sa_json = (merged.get("GCP_SA_KEY") or merged.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") or "").strip()
    if sa_json:
        parsed: dict | None
        try:
            maybe = json.loads(sa_json)
            parsed = maybe if isinstance(maybe, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None and parsed.get("type") == "external_account":
            cfg_path, _, token = materialize_external_account_credentials(
                parsed,
                org_id="default-org",
                workspace_id=environment_id,
            )
            merged["GOOGLE_APPLICATION_CREDENTIALS"] = cfg_path
            merged["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = cfg_path
            merged["LAUNCHPAD_OIDC_JWT"] = token
            merged.pop("GCP_SA_KEY", None)
            merged.pop("GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
            merged.pop("CLOUDSDK_AUTH_ACCESS_TOKEN", None)
            logger.info(
                "gcp_auth_mode",
                mode="external_account",
                environment_id=environment_id,
            )
            return merged

        key_dir = Path(tempfile.gettempdir()) / "launchpad-gcp-keys"
        key_dir.mkdir(parents=True, exist_ok=True)
        key_path = key_dir / f"{_env_slug(environment_id)}.json"
        key_path.write_text(sa_json, encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        merged["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)
        merged["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = str(key_path)
        merged.pop("CLOUDSDK_AUTH_ACCESS_TOKEN", None)
        project = project_id_from_gcp_sa_json(sa_json)
        if project:
            merged.setdefault("CLOUDSDK_CORE_PROJECT", project)
            merged.setdefault("GOOGLE_CLOUD_PROJECT", project)
            merged.setdefault("GCLOUD_PROJECT", project)
        logger.info(
            "gcp_auth_mode",
            mode="service_account",
            environment_id=environment_id,
        )
        return merged

    # Optional Connect OAuth fallback when no SA / WIF key material is present.
    access_token = (merged.get("CLOUDSDK_AUTH_ACCESS_TOKEN") or "").strip()
    if access_token:
        merged.pop("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", None)
        merged.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        logger.info(
            "gcp_auth_mode",
            mode="access_token",
            environment_id=environment_id,
        )
        return merged

    if credentials.gcp_oauth_token_json:
        raise CloudInstanceComputeError(
            "GCP Connect token is present but Launchpad could not mint a "
            "cloud-platform access token, and no service account / WIF key is "
            "configured. Paste a GCP SA JSON (preferred) or set "
            "GCP_OAUTH_CLIENT_ID/SECRET and Connect GCP again."
        )

    return merged


def _env_slug(environment_id: str) -> str:
    return environment_id.replace("-", "")[:12]


def _gcp_zone(region: str) -> str:
    reg = (region or "us-central1").strip() or "us-central1"
    return reg if reg.count("-") >= 2 else f"{reg}-a"


def _gcp_region_from_zone_or_region(value: str) -> str:
    raw = (value or "us-central1").strip() or "us-central1"
    parts = raw.split("-")
    # zone like us-central1-a -> region us-central1
    if len(parts) >= 3 and len(parts[-1]) == 1 and parts[-1].isalpha():
        return "-".join(parts[:-1])
    return raw


def _gcp_zone_candidates(preferred: str) -> list[str]:
    """Prefer the requested zone, then other common letters in the same region."""
    preferred_zone = _gcp_zone(preferred)
    region = _gcp_region_from_zone_or_region(preferred_zone)
    suffixes = ("a", "b", "c", "f")
    ordered = [preferred_zone]
    for suffix in suffixes:
        candidate = f"{region}-{suffix}"
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


_GCP_MACHINE_TYPE_FALLBACKS: tuple[str, ...] = (
    "e2-medium",
    "e2-small",
    "e2-standard-2",
    "n2-standard-2",
    "n1-standard-1",
)


def _is_gcp_capacity_error(detail: str) -> bool:
    lower = (detail or "").lower()
    return any(
        token in lower
        for token in (
            "zone_resource_pool_exhausted",
            "resource_pool_exhausted",
            "does not have enough resources",
            "currently unavailable",
            "quota exceeded",
            "zone_resource_pool",
        )
    )


def _ensure_gcp_artifact_repo(
    *,
    project_id: str,
    region: str,
    env: dict[str, str],
) -> None:
    if shutil.which("gcloud") is None:
        return
    loc = region.rsplit("-", 1)[0] if region.count("-") >= 2 else region
    _run_cmd(
        [
            "gcloud",
            "artifacts",
            "repositories",
            "describe",
            _REPO_NAME,
            f"--location={loc}",
            f"--project={project_id}",
        ],
        timeout=120,
        check=False,
        env=env,
    )
    created = _run_cmd(
        [
            "gcloud",
            "artifacts",
            "repositories",
            "create",
            _REPO_NAME,
            f"--location={loc}",
            f"--project={project_id}",
            "--repository-format=docker",
            "--quiet",
        ],
        timeout=120,
        check=False,
        env=env,
    )
    if created.returncode != 0 and "already exists" not in (created.stderr or "").lower():
        logger.warning(
            "gcp_artifact_repo_create_skipped",
            detail=sanitize_log_message((created.stderr or "")[:200]),
        )


def _docker_auth_gcp(*, region: str, env: dict[str, str]) -> None:
    loc = region.rsplit("-", 1)[0] if region.count("-") >= 2 else region
    host = f"{loc}-docker.pkg.dev"
    _run_cmd(
        ["gcloud", "auth", "configure-docker", host, "--quiet"],
        timeout=120,
        check=False,
        env=env,
    )


def _ensure_aws_ecr_repo(*, region: str, repo: str, env: dict[str, str]) -> str | None:
    if shutil.which("aws") is None:
        return None
    env = {**env, "AWS_DEFAULT_REGION": region, "AWS_REGION": region}
    desc = _run_cmd(
        ["aws", "ecr", "describe-repositories", "--repository-names", repo],
        timeout=120,
        check=False,
        env=env,
    )
    if desc.returncode != 0:
        _run_cmd(
            ["aws", "ecr", "create-repository", "--repository-name", repo],
            timeout=120,
            check=False,
            env=env,
        )
    acct = _run_cmd(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        timeout=60,
        check=False,
        env=env,
    )
    account = (acct.stdout or "").strip()
    if not account:
        return None
    return f"{account}.dkr.ecr.{region}.amazonaws.com/{repo}"


def _docker_auth_aws(*, region: str, env: dict[str, str]) -> None:
    if shutil.which("aws") is None:
        return
    env = {**env, "AWS_DEFAULT_REGION": region, "AWS_REGION": region}
    pw = _run_cmd(
        ["aws", "ecr", "get-login-password", "--region", region],
        timeout=60,
        check=False,
        env=env,
    )
    password = (pw.stdout or "").strip()
    if not password:
        return
    _run_cmd(
        ["docker", "login", "--username", "AWS", "--password-stdin", f"{region}.dkr.ecr.amazonaws.com"],
        timeout=60,
        check=False,
        env=env,
        input_text=password,
    )


def build_and_push_cloud_image(
    *,
    workspace_root: Path,
    environment_id: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    region: str,
    tag: str = "latest",
) -> str:
    """Build workspace Dockerfile locally and push to the target cloud registry."""
    from app.services.attach_deploy import _find_workspace_dockerfile

    found = _find_workspace_dockerfile(workspace_root)
    if found is None:
        raise CloudInstanceComputeError("Workspace has no Dockerfile to build for cloud deploy")
    dockerfile, context = found
    provider = (cloud_provider or CloudProvider.GCP.value).strip().lower()
    slug = _env_slug(environment_id)
    env = _credential_env(credentials, environment_id=environment_id)

    local_tag = f"lp-cloud-build-{slug}:{tag}"
    _run_cmd(
        ["docker", "build", "-t", local_tag, "-f", str(dockerfile), str(context)],
        timeout=900,
        env=env,
    )

    remote: str
    if provider == CloudProvider.GCP.value:
        project_id = resolve_gcp_project_id(credentials=credentials, env=env)
        if not project_id or is_placeholder_gcp_project(project_id):
            raise CloudInstanceComputeError(
                "GCP project id is required to push preview images. Set your real "
                "GCP project id in Settings → GCP project id or the workspace wizard."
            )
        loc = region.rsplit("-", 1)[0] if region.count("-") >= 2 else region
        _ensure_gcp_artifact_repo(project_id=project_id, region=loc, env=env)
        _docker_auth_gcp(region=loc, env=env)
        remote = f"{loc}-docker.pkg.dev/{project_id}/{_REPO_NAME}/{slug}:{tag}"
    elif provider == CloudProvider.AWS.value:
        aws_region = region if region != "us-central1" else "us-east-1"
        repo_uri = _ensure_aws_ecr_repo(region=aws_region, repo=_REPO_NAME, env=env)
        if repo_uri is None:
            raise CloudInstanceComputeError("AWS ECR repository setup failed")
        _docker_auth_aws(region=aws_region, env=env)
        remote = f"{repo_uri}:{tag}"
    elif provider == CloudProvider.AZURE.value:
        raise CloudInstanceComputeError(
            "Azure cloud image push is not configured yet; set an external workload_image"
        )
    else:
        raise CloudInstanceComputeError(f"Cloud image build not supported for provider {provider}")

    _run_cmd(["docker", "tag", local_tag, remote], timeout=60, env=env)
    _run_cmd(["docker", "push", remote], timeout=900, env=env)
    logger.info("cloud_instance_image_pushed", image=remote, provider=provider)
    return remote


def cloud_resource_name(
    *,
    environment_id: str,
    environment_name: str = "",
    base_name: str = "",
    org_slug: str | None = None,
    max_len: int = 55,
) -> str:
    """Stable, unique cloud resource name for an environment (VM / Cloud Run / etc.)."""
    suffix = re.sub(r"[^a-z0-9]", "", (environment_id or "").lower())[:8]
    if not suffix:
        suffix = "preview"
    org = re.sub(r"[^a-z0-9-]+", "-", (org_slug or "").lower()).strip("-")[:16]
    base = (base_name or environment_name or "app").strip() or "app"
    base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-") or "app"
    # lp-{org}-{base}-{suffix} when org present; otherwise {base}-{suffix}.
    prefix = f"lp-{org}-" if org else ""
    reserved = len(prefix) + 1 + len(suffix)
    keep = max(1, max_len - reserved)
    raw = f"{prefix}{base[:keep]}-{suffix}"
    cleaned = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    if cleaned and cleaned[0].isdigit():
        cleaned = f"lp-{cleaned}"
    return (cleaned or f"lp-{suffix}")[:max_len]


def _service_name_for_env(
    environment_name: str,
    environment_id: str,
    *,
    org_slug: str | None = None,
) -> str:
    return cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        org_slug=org_slug,
    )


_PLACEHOLDER_GCP_PROJECTS = frozenset(
    {
        "launchpad-preview",
        "launchpad-previews",
        "my-gcp-project",
        "my-project",
        "your-project",
        "your-gcp-project",
    }
)


def is_placeholder_gcp_project(project_id: str | None) -> bool:
    raw = (project_id or "").strip().lower()
    return bool(raw) and raw in _PLACEHOLDER_GCP_PROJECTS


def resolve_gcp_project_id(
    *,
    wizard_project_id: str | None = None,
    credentials: CloudCredentials | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Pick a real GCP project id (vault / SA / env / wizard), skipping placeholders."""
    candidates: list[str] = []
    if credentials is not None:
        if credentials.gcp_project_id:
            candidates.append(credentials.gcp_project_id.strip())
        sa_project = project_id_from_gcp_sa_json(credentials.gcp_sa_key_json)
        if sa_project:
            candidates.append(sa_project)
    if env:
        for key in ("GCP_PROJECT_ID", "CLOUDSDK_CORE_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
            value = (env.get(key) or "").strip()
            if value:
                candidates.append(value)
    if wizard_project_id and wizard_project_id.strip():
        candidates.append(wizard_project_id.strip())
    for candidate in candidates:
        if candidate and not is_placeholder_gcp_project(candidate):
            return candidate
    # Fall back to the first non-empty value so callers can surface a clear error.
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _apply_gcp_project(env: dict[str, str], project_id: str | None) -> None:
    pid = (project_id or "").strip()
    if not pid:
        return
    # Wizard / explicit project must win over ambient shell CLOUDSDK_CORE_PROJECT.
    env["CLOUDSDK_CORE_PROJECT"] = pid
    env["GOOGLE_CLOUD_PROJECT"] = pid
    env["GCLOUD_PROJECT"] = pid
    env["TF_VAR_project_id"] = pid


def _require_gcp_project_id(env: dict[str, str]) -> str:
    project = _gcp_project_id(env)
    if not project:
        raise CloudInstanceComputeError(
            "GCP project id is not set. Enter your real GCP project id in Settings "
            "(Cloud credentials → GCP project id) or the workspace wizard, then re-provision."
        )
    if is_placeholder_gcp_project(project):
        raise CloudInstanceComputeError(
            f"GCP project id '{project}' is a Launchpad placeholder, not a Google "
            "Cloud project. Set your real project id from "
            "`gcloud projects list` or console.cloud.google.com in Settings → GCP "
            "project id (or the workspace wizard), then re-provision."
        )
    return project


def provision_cloud_vm(
    *,
    running_instance: RunningInstanceConfig,
    environment_id: str,
    environment_name: str,
    cloud_provider: str,
    credentials: CloudCredentials | None,
    listen_port: int,
    org_slug: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
    gcp_project_id: str | None = None,
) -> RunningInstanceConfig:
    """Create a cloud VM when host is not preset. Returns config with host filled."""
    if (running_instance.host or "").strip():
        return running_instance

    # Always env-unique: workspace wizard names collide across previews/orgs.
    instance_name = cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        base_name=running_instance.service_name or environment_name,
        org_slug=org_slug,
    )
    region = (running_instance.region or "us-central1").strip() or "us-central1"
    provider = (cloud_provider or CloudProvider.GCP.value).strip().lower()
    env = _credential_env(credentials, environment_id=environment_id)
    resolved_project = resolve_gcp_project_id(
        wizard_project_id=gcp_project_id,
        credentials=credentials,
        env=env,
    )
    _apply_gcp_project(env, resolved_project)
    want_vpc = bool(create_vpc or create_subnets)
    want_subnets = bool(create_subnets or create_vpc)

    if provider == CloudProvider.GCP.value:
        _require_gcp_project_id(env)
        return _provision_gcp_vm(
            running_instance=running_instance,
            instance_name=instance_name,
            zone=_gcp_zone(region),
            listen_port=listen_port,
            env=env,
            environment_id=environment_id,
            environment_name=environment_name,
            org_slug=org_slug,
            create_vpc=want_vpc,
            create_subnets=want_subnets,
        )
    if provider == CloudProvider.AWS.value:
        aws_region = region if region != "us-central1" else "us-east-1"
        return _provision_aws_vm(
            running_instance=running_instance,
            instance_name=instance_name,
            region=aws_region,
            listen_port=listen_port,
            env=env,
            environment_id=environment_id,
            environment_name=environment_name,
            org_slug=org_slug,
        )
    if provider == CloudProvider.AZURE.value:
        raise CloudInstanceComputeError(
            "Azure VM auto-provision is not configured yet; set running_instance.host"
        )
    raise CloudInstanceComputeError(f"Cloud VM provisioning not supported for {provider}")


def _gcp_project_id(env: dict[str, str]) -> str | None:
    for key in ("CLOUDSDK_CORE_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        value = (env.get(key) or "").strip()
        if value:
            return value
    return None


def _gcloud_project_args(env: dict[str, str]) -> list[str]:
    project = _gcp_project_id(env)
    return [f"--project={project}"] if project else []


_GCP_ALREADY_EXISTS_ZONE_RE = re.compile(
    r"zones/(?P<zone>[a-z0-9-]+)/instances/(?P<name>[a-z0-9-]+)",
    re.IGNORECASE,
)


def _parse_gcp_already_exists(detail: str) -> tuple[str, str] | None:
    """Return (zone, instance_name) from a GCE 'already exists' error when present."""
    match = _GCP_ALREADY_EXISTS_ZONE_RE.search(detail or "")
    if not match:
        return None
    return match.group("zone"), match.group("name")


def _preview_network_names(environment_id: str) -> tuple[str, str]:
    """Stable VPC / subnet names for an environment (GCP)."""
    short = re.sub(r"[^a-z0-9]", "", (environment_id or "").lower())[:12] or "preview"
    return f"lp-net-{short}", f"lp-subnet-{short}"


def _gcp_region_from_zone(zone: str) -> str:
    parts = (zone or "").strip().rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) <= 2:
        return parts[0]
    return (zone or "us-central1").strip() or "us-central1"


def _ensure_gcp_preview_network(
    *,
    environment_id: str,
    zone: str,
    listen_port: int,
    create_subnets: bool,
    env: dict[str, str],
) -> str:
    """Create custom VPC (+ subnet) for a preview VM. Returns network-interface flag."""
    network_name, subnet_name = _preview_network_names(environment_id)
    region = _gcp_region_from_zone(zone)
    project_args = _gcloud_project_args(env)

    created_net = _run_cmd(
        [
            "gcloud",
            "compute",
            "networks",
            "create",
            network_name,
            "--subnet-mode=custom",
            "--quiet",
            *project_args,
        ],
        timeout=180,
        check=False,
        env=env,
    )
    if created_net.returncode != 0:
        detail = (created_net.stderr or created_net.stdout or "").lower()
        if "already exists" not in detail:
            raise CloudInstanceComputeError(
                f"GCP VPC create failed: {sanitize_log_message((created_net.stderr or created_net.stdout or '')[:400])}"
            )

    if create_subnets:
        created_subnet = _run_cmd(
            [
                "gcloud",
                "compute",
                "networks",
                "subnets",
                "create",
                subnet_name,
                f"--network={network_name}",
                f"--region={region}",
                "--range=10.10.0.0/24",
                "--quiet",
                *project_args,
            ],
            timeout=180,
            check=False,
            env=env,
        )
        if created_subnet.returncode != 0:
            detail = (created_subnet.stderr or created_subnet.stdout or "").lower()
            if "already exists" not in detail:
                raise CloudInstanceComputeError(
                    f"GCP subnet create failed: {sanitize_log_message((created_subnet.stderr or created_subnet.stdout or '')[:400])}"
                )

    for rule_name, allows in (
        (f"{network_name}-ssh", "tcp:22"),
        (f"{network_name}-app", f"tcp:{listen_port}"),
    ):
        _run_cmd(
            [
                "gcloud",
                "compute",
                "firewall-rules",
                "create",
                rule_name,
                f"--network={network_name}",
                "--allow",
                allows,
                "--target-tags=lp-preview",
                "--quiet",
                *project_args,
            ],
            timeout=120,
            check=False,
            env=env,
        )

    if create_subnets:
        return f"subnet={subnet_name},network-tier=PREMIUM"
    return f"network={network_name},network-tier=PREMIUM"


def _teardown_gcp_preview_network(
    *,
    environment_id: str,
    preferred_zone: str,
    env: dict[str, str],
) -> None:
    network_name, subnet_name = _preview_network_names(environment_id)
    region = _gcp_region_from_zone(preferred_zone)
    project_args = _gcloud_project_args(env)
    for rule_name in (f"{network_name}-ssh", f"{network_name}-app"):
        _run_cmd(
            [
                "gcloud",
                "compute",
                "firewall-rules",
                "delete",
                rule_name,
                "--quiet",
                *project_args,
            ],
            timeout=120,
            check=False,
            env=env,
        )
    _run_cmd(
        [
            "gcloud",
            "compute",
            "networks",
            "subnets",
            "delete",
            subnet_name,
            f"--region={region}",
            "--quiet",
            *project_args,
        ],
        timeout=180,
        check=False,
        env=env,
    )
    _run_cmd(
        [
            "gcloud",
            "compute",
            "networks",
            "delete",
            network_name,
            "--quiet",
            *project_args,
        ],
        timeout=180,
        check=False,
        env=env,
    )


def _provision_gcp_vm(
    *,
    running_instance: RunningInstanceConfig,
    instance_name: str,
    zone: str,
    listen_port: int,
    env: dict[str, str],
    environment_id: str,
    environment_name: str,
    org_slug: str | None = None,
    create_vpc: bool = False,
    create_subnets: bool = False,
) -> RunningInstanceConfig:
    if shutil.which("gcloud") is None:
        raise CloudInstanceComputeError("gcloud CLI is required for GCP VM provisioning")
    _ = create_subnets  # Custom-mode VPC always provisions a subnet for NIC attach.

    project_args = _gcloud_project_args(env)
    label_env = re.sub(r"[^a-z0-9-]", "-", (environment_id or "").lower()).strip("-")[:63]
    label_name = re.sub(r"[^a-z0-9-]", "-", (environment_name or "preview").lower()).strip("-")[:63]
    label_org = re.sub(r"[^a-z0-9-]", "-", (org_slug or "none").lower()).strip("-")[:63] or "none"
    labels = (
        f"launchpad-environment-id={label_env},"
        f"launchpad-env-name={label_name or 'preview'},"
        f"launchpad-org-slug={label_org},"
        "launchpad-managed=true"
    )

    # Retry after a failed deploy: reuse the leftover VM instead of create.
    existing = _reuse_gcp_vm(
        running_instance=running_instance,
        instance_name=instance_name,
        preferred_zone=zone,
        env=env,
    )
    if existing is not None:
        logger.info(
            "gcp_vm_reused_before_create",
            instance=instance_name,
            zone=existing.region,
            host=existing.host,
        )
        return existing

    # Finish ALL apt/docker work before writing vm-ready. SSH bootstrap races
    # apt locks if we mark ready while get.docker.com is still running.
    startup = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "apt-get update -y\n"
        "apt-get install -y --no-install-recommends ca-certificates curl git "
        "build-essential python3 python3-pip python3-venv ufw psmisc\n"
        "if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then\n"
        "  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -\n"
        "  apt-get install -y nodejs\n"
        "fi\n"
        "npm install -g pm2 || true\n"
        "if ! command -v docker >/dev/null 2>&1; then\n"
        "  curl -fsSL https://get.docker.com | sh || true\n"
        "  systemctl enable docker || true\n"
        "  systemctl start docker || true\n"
        "fi\n"
        "mkdir -p /var/lib/launchpad\n"
        "touch /var/lib/launchpad/vm-ready\n"
    )

    nic = "network=default,network-tier=PREMIUM"
    if create_vpc:
        # Custom-mode VPC requires a subnet for instance NICs.
        nic = _ensure_gcp_preview_network(
            environment_id=environment_id,
            zone=zone,
            listen_port=listen_port,
            create_subnets=True,
            env=env,
        )
    else:
        _run_cmd(
            [
                "gcloud",
                "compute",
                "firewall-rules",
                "create",
                "lp-preview-allow-app",
                "--allow",
                f"tcp:{listen_port}",
                "--target-tags=lp-preview",
                "--quiet",
                *project_args,
            ],
            timeout=120,
            check=False,
            env=env,
        )

    zones = _gcp_zone_candidates(zone)
    last_detail = "create failed"
    chosen_zone: str | None = None
    chosen_machine: str | None = None

    for machine_type in _GCP_MACHINE_TYPE_FALLBACKS:
        for candidate_zone in zones:
            created = _run_cmd(
                [
                    "gcloud",
                    "compute",
                    "instances",
                    "create",
                    instance_name,
                    f"--zone={candidate_zone}",
                    f"--machine-type={machine_type}",
                    "--image-family=ubuntu-2204-lts",
                    "--image-project=ubuntu-os-cloud",
                    "--tags=lp-preview",
                    f"--labels={labels}",
                    # Ephemeral public IP so preview URL + SSH work after create.
                    f"--network-interface={nic}",
                    f"--metadata=startup-script={startup}",
                    "--format=json",
                    "--quiet",
                    *project_args,
                ],
                timeout=600,
                check=False,
                env=env,
            )
            if created.returncode == 0:
                chosen_zone = candidate_zone
                chosen_machine = machine_type
                break

            last_detail = sanitize_log_message(
                (created.stderr or created.stdout or "create failed")[:500]
            )
            detail_l = last_detail.lower()
            if "already exists" in detail_l:
                parsed = _parse_gcp_already_exists(last_detail)
                reuse_zone = parsed[0] if parsed else candidate_zone
                reuse_name = parsed[1] if parsed else instance_name
                reused = _reuse_gcp_vm(
                    running_instance=running_instance,
                    instance_name=reuse_name,
                    preferred_zone=reuse_zone,
                    env=env,
                    required=True,
                )
                if reused is not None:
                    logger.info(
                        "gcp_vm_reused",
                        instance=reuse_name,
                        zone=reused.region,
                        host=reused.host,
                    )
                    return reused
                raise CloudInstanceComputeError(
                    f"GCP VM {reuse_name} already exists in {reuse_zone} but could not "
                    "read its IP/status for retry. Check gcloud auth/project and retry."
                )
            if _is_gcp_capacity_error(last_detail):
                logger.warning(
                    "gcp_vm_capacity_retry",
                    zone=candidate_zone,
                    machine_type=machine_type,
                    detail=last_detail[:200],
                )
                continue
            # Non-capacity errors (permissions, naming, etc.) fail fast.
            raise CloudInstanceComputeError(f"GCP VM create failed: {last_detail}")
        if chosen_zone is not None:
            break

    if chosen_zone is None or chosen_machine is None:
        reused = _reuse_gcp_vm(
            running_instance=running_instance,
            instance_name=instance_name,
            preferred_zone=zone,
            env=env,
        )
        if reused is not None:
            logger.info(
                "gcp_vm_reused_after_create_exhausted",
                instance=instance_name,
                zone=reused.region,
                host=reused.host,
            )
            return reused
        raise CloudInstanceComputeError(
            "GCP VM create failed after trying alternate zones/machine types: "
            f"{last_detail}"
        )

    logger.info(
        "gcp_vm_created",
        instance=instance_name,
        zone=chosen_zone,
        machine_type=chosen_machine,
    )

    host = _wait_gcp_instance_host(
        instance_name=instance_name,
        zone=chosen_zone,
        env=env,
        attempts=24,
    )
    if not host:
        # gcloud compute ssh still works with instance name + zone.
        host = instance_name

    return running_instance.model_copy(
        update={
            "host": host,
            "service_name": instance_name,
            "region": chosen_zone,
            "ssh_user": running_instance.ssh_user or "ubuntu",
        }
    )


def _gcp_instance_describe_json(
    *,
    instance_name: str,
    zone: str,
    env: dict[str, str],
) -> dict | None:
    desc = _run_cmd(
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            instance_name,
            f"--zone={zone}",
            "--format=json",
            *_gcloud_project_args(env),
        ],
        timeout=120,
        check=False,
        env=env,
    )
    if desc.returncode != 0:
        logger.warning(
            "gcp_vm_describe_failed",
            instance=instance_name,
            zone=zone,
            detail=sanitize_log_message((desc.stderr or desc.stdout or "")[:300]),
        )
        return None
    try:
        payload = json.loads(desc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _gcp_host_from_instance(payload: dict) -> str:
    nics = payload.get("networkInterfaces") or []
    if not isinstance(nics, list):
        return ""
    for nic in nics:
        if not isinstance(nic, dict):
            continue
        for access in nic.get("accessConfigs") or []:
            if isinstance(access, dict):
                nat = str(access.get("natIP") or "").strip()
                if nat:
                    return nat
        internal = str(nic.get("networkIP") or "").strip()
        if internal:
            return internal
    return ""


def _gcp_instance_external_ip(
    *,
    instance_name: str,
    zone: str,
    env: dict[str, str],
) -> str:
    payload = _gcp_instance_describe_json(
        instance_name=instance_name,
        zone=zone,
        env=env,
    )
    if not payload:
        return ""
    return _gcp_host_from_instance(payload)


def _wait_gcp_instance_host(
    *,
    instance_name: str,
    zone: str,
    env: dict[str, str],
    attempts: int = 12,
) -> str:
    for _ in range(max(1, attempts)):
        host = _gcp_instance_external_ip(
            instance_name=instance_name,
            zone=zone,
            env=env,
        )
        if host:
            return host
        time.sleep(5)
    return ""


def _find_gcp_instance_via_list(
    *,
    instance_name: str,
    env: dict[str, str],
) -> tuple[str, dict] | None:
    """Find an instance by name across zones (best for retry reuse)."""
    listed = _run_cmd(
        [
            "gcloud",
            "compute",
            "instances",
            "list",
            f"--filter=name={instance_name}",
            "--format=json",
            *_gcloud_project_args(env),
        ],
        timeout=120,
        check=False,
        env=env,
    )
    if listed.returncode != 0:
        logger.warning(
            "gcp_vm_list_failed",
            instance=instance_name,
            detail=sanitize_log_message((listed.stderr or listed.stdout or "")[:300]),
        )
        return None
    try:
        rows = json.loads(listed.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict):
        return None
    zone_url = str(row.get("zone") or "")
    zone = zone_url.rsplit("/", 1)[-1].strip()
    if not zone:
        return None
    return zone, row


def _reuse_gcp_vm(
    *,
    running_instance: RunningInstanceConfig,
    instance_name: str,
    preferred_zone: str,
    env: dict[str, str],
    required: bool = False,
) -> RunningInstanceConfig | None:
    """Locate an existing instance by name and return config with host filled."""
    found = _find_gcp_instance_via_list(instance_name=instance_name, env=env)
    if found is not None:
        zone, row = found
        host = _gcp_host_from_instance(row) or _wait_gcp_instance_host(
            instance_name=instance_name,
            zone=zone,
            env=env,
            attempts=12 if required else 3,
        )
        if not host:
            host = instance_name
        return running_instance.model_copy(
            update={
                "host": host,
                "service_name": instance_name,
                "region": zone,
                "ssh_user": running_instance.ssh_user or "ubuntu",
            }
        )

    zones = _gcp_zone_candidates(preferred_zone)
    # Prefer the exact zone from an already-exists error first.
    preferred_exact = _gcp_zone(preferred_zone)
    if preferred_exact in zones:
        zones = [preferred_exact, *[z for z in zones if z != preferred_exact]]

    for candidate_zone in zones:
        payload = _gcp_instance_describe_json(
            instance_name=instance_name,
            zone=candidate_zone,
            env=env,
        )
        if payload is None:
            continue
        host = _gcp_host_from_instance(payload)
        if not host:
            host = _wait_gcp_instance_host(
                instance_name=instance_name,
                zone=candidate_zone,
                env=env,
                attempts=12 if required else 3,
            )
        if not host:
            host = instance_name
        return running_instance.model_copy(
            update={
                "host": host,
                "service_name": instance_name,
                "region": candidate_zone,
                "ssh_user": running_instance.ssh_user or "ubuntu",
            }
        )
    return None


def _aws_instance_public_ip(*, instance_id: str, region: str, env: dict[str, str]) -> str:
    res = _run_cmd(
        [
            "aws",
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--region",
            region,
            "--query",
            "Reservations[0].Instances[0].PublicIpAddress",
            "--output",
            "text",
        ],
        timeout=60,
        check=False,
        env=env,
    )
    host = (res.stdout or "").strip()
    # `--output text` prints the literal "None" for an unassigned IP.
    if not host or host.lower() == "none":
        return ""
    return host


def _wait_aws_instance_ip(
    *,
    instance_id: str,
    region: str,
    env: dict[str, str],
    attempts: int = 24,
) -> str:
    """Poll describe-instances until the public IP is assigned (or attempts run out)."""
    for _ in range(max(1, attempts)):
        host = _aws_instance_public_ip(instance_id=instance_id, region=region, env=env)
        if host:
            return host
        time.sleep(5)
    return ""


def _provision_aws_vm(
    *,
    running_instance: RunningInstanceConfig,
    instance_name: str,
    region: str,
    listen_port: int,
    env: dict[str, str],
    environment_id: str,
    environment_name: str,
    org_slug: str | None = None,
) -> RunningInstanceConfig:
    if shutil.which("aws") is None:
        raise CloudInstanceComputeError("aws CLI is required for AWS VM provisioning")
    env = {**env, "AWS_DEFAULT_REGION": region, "AWS_REGION": region}

    user_data = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "dnf install -y git curl python3 python3-pip || yum install -y git curl python3 python3-pip\n"
        "if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then\n"
        "  curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -\n"
        "  dnf install -y nodejs || yum install -y nodejs\n"
        "fi\n"
        "npm install -g pm2 || true\n"
        "dnf install -y docker || yum install -y docker || true\n"
        "systemctl enable docker || true\n"
        "systemctl start docker || true\n"
        "mkdir -p /var/lib/launchpad\n"
        "touch /var/lib/launchpad/vm-ready\n"
    )

    sg = _run_cmd(
        [
            "aws",
            "ec2",
            "create-security-group",
            "--group-name",
            "lp-preview-sg",
            "--description",
            "Launchpad preview instances",
            "--query",
            "GroupId",
            "--output",
            "text",
        ],
        timeout=120,
        check=False,
        env=env,
    )
    sg_id = (sg.stdout or "").strip()
    if sg_id:
        _run_cmd(
            [
                "aws",
                "ec2",
                "authorize-security-group-ingress",
                "--group-id",
                sg_id,
                "--protocol",
                "tcp",
                "--port",
                str(listen_port),
                "--cidr",
                "0.0.0.0/0",
            ],
            timeout=60,
            check=False,
            env=env,
        )

    run = _run_cmd(
        [
            "aws",
            "ec2",
            "run-instances",
            "--image-id",
            "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
            "--instance-type",
            "t3.small",
            "--count",
            "1",
            "--tag-specifications",
            json.dumps(
                [
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": instance_name},
                            {"Key": "launchpad-preview", "Value": "true"},
                            {"Key": "launchpad-environment-id", "Value": environment_id},
                            {
                                "Key": "launchpad-env-name",
                                "Value": (environment_name or "preview")[:256],
                            },
                            {
                                "Key": "launchpad-org-slug",
                                "Value": (org_slug or "none")[:256],
                            },
                            {"Key": "launchpad-managed", "Value": "true"},
                        ],
                    }
                ]
            ),
            *(["--security-group-ids", sg_id] if sg_id else []),
            "--user-data",
            user_data,
            "--query",
            "Instances[0].InstanceId",
            "--output",
            "text",
        ],
        timeout=600,
        check=False,
        env=env,
    )
    instance_id = (run.stdout or "").strip()
    if run.returncode != 0 or not instance_id:
        detail = sanitize_log_message((run.stderr or run.stdout or "run-instances failed")[:500])
        raise CloudInstanceComputeError(f"AWS EC2 create failed: {detail}")

    _run_cmd(
        ["aws", "ec2", "wait", "instance-running", "--instance-ids", instance_id],
        timeout=600,
        check=False,
        env=env,
    )
    # The public IP is often not yet populated in describe-instances the instant the
    # instance reports "running" - poll instead of failing on the first empty read,
    # so a preview does not need to be retried by hand.
    host = _wait_aws_instance_ip(
        instance_id=instance_id,
        region=region,
        env=env,
        attempts=24,
    )
    if not host:
        raise CloudInstanceComputeError(
            "EC2 instance is running but no public IP was assigned within the wait "
            "window. Ensure the subnet auto-assigns public IPs (map-public-ip-on-launch) "
            "or attach an Elastic IP, then retry."
        )

    return running_instance.model_copy(
        update={
            "host": host,
            "service_name": instance_id,
            "region": region,
            "ssh_user": "ec2-user",
        }
    )


def teardown_cloud_vm(
    *,
    running_instance: RunningInstanceConfig,
    environment_id: str,
    environment_name: str,
    cloud_provider: str | None,
    credentials: CloudCredentials | None,
    org_slug: str | None = None,
) -> None:
    """Delete cloud VM resources created for an instance preview."""
    provider = (cloud_provider or CloudProvider.LOCAL.value).strip().lower()
    if provider == CloudProvider.LOCAL.value:
        return

    env = _credential_env(credentials, environment_id=environment_id)
    region = (running_instance.region or "us-central1").strip() or "us-central1"
    unique = cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        base_name=running_instance.service_name or environment_name,
        org_slug=org_slug,
    )
    legacy = cloud_resource_name(
        environment_id=environment_id,
        environment_name=environment_name,
        base_name=running_instance.service_name or environment_name,
    )
    candidates: list[str] = []
    for name in (
        unique,
        legacy,
        (running_instance.service_name or "").strip(),
        _service_name_for_env(environment_name, environment_id, org_slug=org_slug),
    ):
        if name and name not in candidates:
            candidates.append(name)

    if provider == CloudProvider.GCP.value and shutil.which("gcloud"):
        _teardown_gcp_vms(
            candidates=candidates,
            preferred_zone=_gcp_zone(region),
            environment_id=environment_id,
            env=env,
        )
        # Give GCE time to release NICs before deleting custom VPC/subnet.
        time.sleep(8)
        _teardown_gcp_preview_network(
            environment_id=environment_id,
            preferred_zone=_gcp_zone(region),
            env=env,
        )
        return

    if provider == CloudProvider.AWS.value and shutil.which("aws"):
        aws_region = region if region != "us-central1" else "us-east-1"
        env = {**env, "AWS_DEFAULT_REGION": aws_region, "AWS_REGION": aws_region}
        _teardown_aws_vms(
            candidates=candidates,
            environment_id=environment_id,
            env=env,
        )
        return

    logger.info(
        "cloud_vm_teardown_noop",
        provider=provider,
        candidates=candidates,
        environment_id=environment_id,
    )


def _teardown_gcp_vms(
    *,
    candidates: list[str],
    preferred_zone: str,
    environment_id: str,
    env: dict[str, str],
) -> None:
    project_args = _gcloud_project_args(env)
    deleted: set[str] = set()

    def _delete(name: str, zone: str) -> None:
        key = f"{zone}/{name}"
        if key in deleted:
            return
        result = _run_cmd(
            [
                "gcloud",
                "compute",
                "instances",
                "delete",
                name,
                f"--zone={zone}",
                "--quiet",
                *project_args,
            ],
            timeout=300,
            check=False,
            env=env,
        )
        deleted.add(key)
        logger.info(
            "gcp_vm_teardown_delete",
            instance=name,
            zone=zone,
            ok=result.returncode == 0,
            environment_id=environment_id,
        )

    # 1) Label filter (preferred for env-scoped cleanup).
    label = re.sub(r"[^a-z0-9-]", "-", (environment_id or "").lower()).strip("-")[:63]
    if label:
        listed = _run_cmd(
            [
                "gcloud",
                "compute",
                "instances",
                "list",
                f"--filter=labels.launchpad-environment-id={label}",
                "--format=json",
                *project_args,
            ],
            timeout=120,
            check=False,
            env=env,
        )
        if listed.returncode == 0:
            try:
                rows = json.loads(listed.stdout or "[]")
            except json.JSONDecodeError:
                rows = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "").strip()
                    zone_url = str(row.get("zone") or "")
                    zone = zone_url.rsplit("/", 1)[-1].strip()
                    if name and zone:
                        _delete(name, zone)

    # 2) Name candidates across zones / list-by-name.
    for name in candidates:
        found = _find_gcp_instance_via_list(instance_name=name, env=env)
        if found is not None:
            zone, _row = found
            _delete(name, zone)
            continue
        for zone in _gcp_zone_candidates(preferred_zone):
            _delete(name, zone)


def _teardown_aws_vms(
    *,
    candidates: list[str],
    environment_id: str,
    env: dict[str, str],
) -> None:
    instance_ids: list[str] = []

    # Tag filter first.
    by_tag = _run_cmd(
        [
            "aws",
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag:launchpad-environment-id,Values={environment_id}",
            "Name=instance-state-name,Values=running,pending,stopping,stopped",
            "--query",
            "Reservations[].Instances[].InstanceId",
            "--output",
            "text",
        ],
        timeout=120,
        check=False,
        env=env,
    )
    if by_tag.returncode == 0 and (by_tag.stdout or "").strip():
        instance_ids.extend((by_tag.stdout or "").split())

    for candidate in candidates:
        if candidate.startswith("i-"):
            instance_ids.append(candidate)
            continue
        listed = _run_cmd(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--filters",
                f"Name=tag:Name,Values={candidate}",
                "Name=instance-state-name,Values=running,pending,stopping,stopped",
                "--query",
                "Reservations[].Instances[].InstanceId",
                "--output",
                "text",
            ],
            timeout=120,
            check=False,
            env=env,
        )
        if listed.returncode == 0 and (listed.stdout or "").strip():
            instance_ids.extend((listed.stdout or "").split())

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_ids: list[str] = []
    for iid in instance_ids:
        token = iid.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        unique_ids.append(token)

    if not unique_ids:
        logger.info(
            "aws_vm_teardown_none_found",
            environment_id=environment_id,
            candidates=candidates,
        )
        return

    _run_cmd(
        ["aws", "ec2", "terminate-instances", "--instance-ids", *unique_ids],
        timeout=300,
        check=False,
        env=env,
    )
    logger.info(
        "aws_vm_teardown_terminate",
        environment_id=environment_id,
        instance_ids=unique_ids,
    )

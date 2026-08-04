"""Secret encryption and sandbox credential materialization.

Static cloud keys are Fernet-encrypted at rest. Prefer keyless OIDC
(GCP Workload Identity Federation / AWS IAM Roles Anywhere web identity)
when WIF fields are present - short-lived JWTs instead of long-lived SA JSON.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.cloud import CloudCredentials

logger = get_logger(__name__)

_SENSITIVE_ENV_KEYS = {
    "GCP_SA_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "CLOUDFLARE_API_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "KUBECONFIG",
    "LAUNCHPAD_OIDC_JWT",
}

_VALUE_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization|credential|"
    r"aws_secret_access_key|aws_access_key_id|gcp_sa_key|private[_-]?key)"
    r"\s*[:=]\s*\S+"
)

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*")
_GITHUB_TOKEN_PATTERN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


def _derive_fernet_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key_material = settings.secrets_encryption_key
    if not key_material:
        # Ephemeral key for local/dev - never use empty key in production.
        key_material = os.environ.get("LAUNCHPAD_DEV_SECRET_KEY") or secrets.token_urlsafe(32)
        logger.warning("secrets_encryption_key_missing_using_ephemeral_dev_key")
    return Fernet(_derive_fernet_key(key_material))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret - invalid key or ciphertext") from exc


def mask_secret_value(value: str, *, visible: int = 4) -> str:
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * max(8, len(value) - visible)}{value[-visible:]}"


def mask_terminal_output(chunk: str) -> str:
    masked = _VALUE_PATTERN.sub(r"\1=[REDACTED]", chunk)
    masked = _BEARER_PATTERN.sub(r"\1[REDACTED]", masked)
    masked = _GITHUB_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", masked)
    for key in _SENSITIVE_ENV_KEYS:
        masked = re.sub(
            rf"(?i)({re.escape(key)}\s*[:=]\s*)\S+",
            r"\1[REDACTED]",
            masked,
        )
    return masked


def project_id_from_gcp_sa_json(sa_json: str | None) -> str | None:
    """Extract GCP project_id from a service-account JSON key (never log the key)."""
    if not sa_json or not sa_json.strip():
        return None
    try:
        data = json.loads(sa_json)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    project = data.get("project_id")
    if isinstance(project, str) and project.strip():
        return project.strip()
    return None


def gcp_wif_complete(creds: CloudCredentials | Mapping[str, str | None]) -> bool:
    """True when all four GCP WIF fields are present."""
    if isinstance(creds, CloudCredentials):
        return bool(
            creds.gcp_wif_project_number
            and creds.gcp_wif_pool_id
            and creds.gcp_wif_provider_id
            and creds.gcp_wif_target_sa_email
        )
    return bool(
        creds.get("gcp_wif_project_number")
        or creds.get("GCP_WIF_PROJECT_NUMBER")
    ) and bool(
        creds.get("gcp_wif_pool_id") or creds.get("GCP_WIF_POOL_ID")
    ) and bool(
        creds.get("gcp_wif_provider_id") or creds.get("GCP_WIF_PROVIDER_ID")
    ) and bool(
        creds.get("gcp_wif_target_sa_email") or creds.get("GCP_WIF_TARGET_SA_EMAIL")
    )


def has_gcp_auth(creds: CloudCredentials) -> bool:
    """GCP auth is satisfied by SA JSON or complete WIF config."""
    return bool(creds.gcp_sa_key_json) or gcp_wif_complete(creds)


def has_aws_auth(creds: CloudCredentials) -> bool:
    """AWS auth is satisfied by access keys or a role ARN for web identity."""
    if creds.aws_role_arn:
        return True
    return bool(creds.aws_access_key_id and creds.aws_secret_access_key)


def validate_cloud_credentials(provider: str, creds: CloudCredentials) -> None:
    """Raise ValueError when the credentials for a cloud provider are incomplete.

    Shared by the preview-launch and promote request validators so the per-provider
    rules and messages live in one place. ``provider`` may be a PreviewProvider
    member or its string value ("gcp"/"aws"/...), which compare equal here.
    """
    if provider == "gcp" and not has_gcp_auth(creds):
        raise ValueError(
            "GCP credentials required: service account JSON or complete Workload Identity Federation config"
        )
    if provider == "aws" and not has_aws_auth(creds):
        raise ValueError(
            "AWS credentials required: access key + secret, or IAM role ARN for keyless OIDC"
        )
    if provider == "azure" and (
        not creds.azure_client_id
        or not creds.azure_client_secret
        or not creds.azure_tenant_id
        or not creds.azure_subscription_id
    ):
        raise ValueError("Azure service principal fields are required")
    if provider == "cloudflare" and not creds.cloudflare_api_token:
        raise ValueError("Cloudflare API token is required")


def cloud_credentials_to_map(credentials: CloudCredentials) -> dict[str, str | None]:
    """Flatten CloudCredentials for sandbox env materialization."""
    return {
        "GCP_SA_KEY": credentials.gcp_sa_key_json,
        "GCP_WIF_PROJECT_NUMBER": credentials.gcp_wif_project_number,
        "GCP_WIF_POOL_ID": credentials.gcp_wif_pool_id,
        "GCP_WIF_PROVIDER_ID": credentials.gcp_wif_provider_id,
        "GCP_WIF_TARGET_SA_EMAIL": credentials.gcp_wif_target_sa_email,
        "AWS_ACCESS_KEY_ID": credentials.aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": credentials.aws_secret_access_key,
        "AWS_SESSION_TOKEN": credentials.aws_session_token,
        "AWS_ROLE_ARN": credentials.aws_role_arn,
        "AWS_ROLE_SESSION_NAME": credentials.aws_role_session_name,
        "AZURE_CLIENT_ID": credentials.azure_client_id,
        "AZURE_CLIENT_SECRET": credentials.azure_client_secret,
        "AZURE_TENANT_ID": credentials.azure_tenant_id,
        "AZURE_SUBSCRIPTION_ID": credentials.azure_subscription_id,
        "CLOUDFLARE_API_TOKEN": credentials.cloudflare_api_token,
    }


def _oidc_workdir(workspace_id: str) -> Path:
    root = Path(tempfile.gettempdir()) / "launchpad-oidc" / workspace_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def credentials_to_env(
    credentials_map: dict[str, str | None] | CloudCredentials,
    *,
    org_id: str = "default-org",
    workspace_id: str = "default-ws",
    env_type: str = "production",
) -> dict[str, str]:
    """Build sandbox env vars, omitting empty values. Never log return values.

    When GCP WIF or AWS role ARN fields are present, mint a short-lived OIDC
    JWT and set ADC / web-identity env vars. Static SA/access keys are omitted
    in that case so sandboxes prefer keyless auth.
    """
    if isinstance(credentials_map, CloudCredentials):
        credentials_map = cloud_credentials_to_map(credentials_map)

    env: dict[str, str] = {}
    for key, value in credentials_map.items():
        if value:
            env[key] = value

    settings = get_settings()
    workdir = _oidc_workdir(workspace_id)

    gcp_wif_proj = env.get("gcp_wif_project_number") or env.get("GCP_WIF_PROJECT_NUMBER")
    gcp_wif_pool = env.get("gcp_wif_pool_id") or env.get("GCP_WIF_POOL_ID")
    gcp_wif_provider = env.get("gcp_wif_provider_id") or env.get("GCP_WIF_PROVIDER_ID")
    gcp_wif_sa = env.get("gcp_wif_target_sa_email") or env.get("GCP_WIF_TARGET_SA_EMAIL")

    if gcp_wif_proj and gcp_wif_pool and gcp_wif_provider and gcp_wif_sa:
        from pkg.auth.oidc.token_engine import OidcTokenEngine
        from pkg.sandbox.exec import CredentialInjector, GcpWifConfig

        injector = CredentialInjector(
            token_engine=OidcTokenEngine(issuer_url=settings.launchpad_oidc_issuer_url),
        )
        token_path = workdir / "oidc_token.jwt"
        cfg_path = workdir / "gcp_credential_config.json"
        res = injector.inject_gcp_wif(
            org_id=org_id,
            workspace_id=workspace_id,
            env_type=env_type,
            wif_config=GcpWifConfig(
                project_number=gcp_wif_proj,
                pool_id=gcp_wif_pool,
                provider_id=gcp_wif_provider,
                target_sa_email=gcp_wif_sa,
                token_file_path=str(token_path),
                credential_config_path=str(cfg_path),
            ),
        )
        env.update(res.env_vars)
        # Prefer keyless - do not expose long-lived SA JSON alongside WIF.
        env.pop("GCP_SA_KEY", None)
        for meta in (
            "GCP_WIF_PROJECT_NUMBER",
            "GCP_WIF_POOL_ID",
            "GCP_WIF_PROVIDER_ID",
            "GCP_WIF_TARGET_SA_EMAIL",
            "gcp_wif_project_number",
            "gcp_wif_pool_id",
            "gcp_wif_provider_id",
            "gcp_wif_target_sa_email",
        ):
            env.pop(meta, None)

    aws_role_arn = env.get("aws_role_arn") or env.get("AWS_ROLE_ARN")
    aws_role_session = env.get("aws_role_session_name") or env.get("AWS_ROLE_SESSION_NAME")

    if aws_role_arn and "AWS_WEB_IDENTITY_TOKEN_FILE" not in env:
        from pkg.auth.oidc.token_engine import OidcTokenEngine
        from pkg.sandbox.exec import AwsWebIdentityConfig, CredentialInjector

        injector = CredentialInjector(
            token_engine=OidcTokenEngine(issuer_url=settings.launchpad_oidc_issuer_url),
        )
        token_path = workdir / "aws_oidc_token.jwt"
        res = injector.inject_aws_web_identity(
            org_id=org_id,
            workspace_id=workspace_id,
            env_type=env_type,
            aws_config=AwsWebIdentityConfig(
                role_arn=aws_role_arn,
                role_session_name=aws_role_session,
                token_file_path=str(token_path),
            ),
        )
        env.update(res.env_vars)
        # Prefer keyless - strip static AWS keys when assuming a role via OIDC.
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "aws_role_arn",
            "aws_role_session_name",
        ):
            if key in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}:
                env.pop(key, None)
            elif key.startswith("aws_"):
                env.pop(key, None)

    # Prefer the project_id embedded in the GCP SA key over workspace form defaults.
    gcp_project = project_id_from_gcp_sa_json(env.get("GCP_SA_KEY"))
    if gcp_project:
        env.setdefault("TF_VAR_project_id", gcp_project)
        env.setdefault("GOOGLE_CLOUD_PROJECT", gcp_project)
        env.setdefault("CLOUDSDK_CORE_PROJECT", gcp_project)
        env.setdefault("GCLOUD_PROJECT", gcp_project)
    return env

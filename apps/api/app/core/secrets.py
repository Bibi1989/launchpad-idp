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


def _parse_gcp_credential_json(raw: str | None) -> dict[str, object] | None:
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


_MARKDOWN_LINK_RE = re.compile(
    r"^\[(?P<label>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)\s*$",
    re.IGNORECASE,
)


def unwrap_markdown_url(value: str) -> str:
    """Strip accidental markdown link wrapping from copied URLs.

    Chat/docs paste often yields ``[https://...](https://...)`` which breaks
    HTTP clients (``No connection adapters were found for '[https://...]'``).
    """
    raw = (value or "").strip()
    if not raw:
        return raw
    match = _MARKDOWN_LINK_RE.match(raw)
    if match:
        return match.group("url").strip()
    return raw


def sanitize_gcp_external_account(config: dict[str, object]) -> dict[str, object]:
    """Normalize URL fields on a GCP external_account credential config."""
    rewritten = dict(config)
    for key in (
        "token_url",
        "service_account_impersonation_url",
        "token_info_url",
        "universe_domain",
    ):
        current = rewritten.get(key)
        if isinstance(current, str) and current.strip():
            rewritten[key] = unwrap_markdown_url(current)
    return rewritten


def materialize_external_account_credentials(
    config: dict[str, object],
    *,
    org_id: str,
    workspace_id: str,
    env_type: str = "production",
) -> tuple[str, str, str]:
    """Rewrite a GCP external_account config onto a fresh Launchpad OIDC JWT.

    Returns ``(credential_config_path, token_path, token)``.
    """
    settings = get_settings()
    workdir = _oidc_workdir(workspace_id)
    token_path = workdir / "oidc_token.jwt"
    cfg_path = workdir / "gcp_credential_config.json"

    from pkg.auth.oidc.token_engine import OidcTokenEngine, TokenRequest

    token = OidcTokenEngine(
        issuer_url=settings.launchpad_oidc_issuer_url,
    ).generate_token(
        TokenRequest(
            org_id=org_id,
            workspace_id=workspace_id,
            env_type=env_type,
            provider="gcp",
            ttl_seconds=int(getattr(settings, "launchpad_oidc_token_ttl_seconds", 900) or 900),
        )
    )
    token_path.write_text(token, encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass

    rewritten = sanitize_gcp_external_account(config)
    cred_src = rewritten.get("credential_source")
    if not isinstance(cred_src, dict):
        cred_src = {}
    else:
        cred_src = dict(cred_src)
    cred_src["file"] = str(token_path)
    rewritten["credential_source"] = cred_src
    if not str(rewritten.get("token_url") or "").strip():
        rewritten["token_url"] = "https://sts.googleapis.com/v1/token"
    # Explicit scopes avoid "insufficient authentication scopes" on compute APIs.
    existing_scopes = rewritten.get("scopes")
    if not isinstance(existing_scopes, list) or not existing_scopes:
        rewritten["scopes"] = ["https://www.googleapis.com/auth/cloud-platform"]
    cfg_path.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")
    try:
        os.chmod(cfg_path, 0o600)
    except OSError:
        pass
    return str(cfg_path), str(token_path), token


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
    """GCP auth is satisfied by SA JSON, WIF config, or interactive OAuth tokens."""
    return (
        bool(creds.gcp_sa_key_json)
        or gcp_wif_complete(creds)
        or bool(creds.gcp_oauth_token_json)
    )


def has_aws_auth(creds: CloudCredentials) -> bool:
    """AWS auth is satisfied by access keys, role ARN, or interactive SSO tokens."""
    if creds.aws_role_arn:
        return True
    if creds.aws_oauth_token_json:
        return True
    return bool(creds.aws_access_key_id and creds.aws_secret_access_key)


def has_azure_auth(creds: CloudCredentials) -> bool:
    """Azure auth is satisfied by service principal or interactive Entra OAuth."""
    if creds.azure_oauth_token_json:
        return True
    return bool(
        creds.azure_client_id
        and creds.azure_client_secret
        and creds.azure_tenant_id
        and creds.azure_subscription_id
    )


def validate_cloud_credentials(provider: str, creds: CloudCredentials) -> None:
    """Raise ValueError when the credentials for a cloud provider are incomplete.

    Shared by the preview-launch and promote request validators so the per-provider
    rules and messages live in one place. ``provider`` may be a PreviewProvider
    member or its string value ("gcp"/"aws"/...), which compare equal here.
    """
    if provider == "gcp" and not has_gcp_auth(creds):
        raise ValueError(
            "GCP credentials required: service account JSON, WIF config, or Connect Google Cloud"
        )
    if provider == "aws" and not has_aws_auth(creds):
        raise ValueError(
            "AWS credentials required: access keys, role ARN, or Connect AWS SSO"
        )
    if provider == "azure" and not has_azure_auth(creds):
        raise ValueError(
            "Azure credentials required: service principal fields or Connect Microsoft"
        )
    if provider == "cloudflare" and not creds.cloudflare_api_token:
        raise ValueError("Cloudflare API token is required")


def cloud_credentials_to_map(credentials: CloudCredentials) -> dict[str, str | None]:
    """Flatten CloudCredentials for sandbox env materialization."""
    return {
        "GCP_SA_KEY": credentials.gcp_sa_key_json,
        "GCP_PROJECT_ID": credentials.gcp_project_id,
        "GCP_WIF_PROJECT_NUMBER": credentials.gcp_wif_project_number,
        "GCP_WIF_POOL_ID": credentials.gcp_wif_pool_id,
        "GCP_WIF_PROVIDER_ID": credentials.gcp_wif_provider_id,
        "GCP_WIF_TARGET_SA_EMAIL": credentials.gcp_wif_target_sa_email,
        "GCP_OAUTH_TOKEN_JSON": credentials.gcp_oauth_token_json,
        "AWS_ACCESS_KEY_ID": credentials.aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": credentials.aws_secret_access_key,
        "AWS_SESSION_TOKEN": credentials.aws_session_token,
        "AWS_ROLE_ARN": credentials.aws_role_arn,
        "AWS_ROLE_SESSION_NAME": credentials.aws_role_session_name,
        "AWS_OAUTH_TOKEN_JSON": credentials.aws_oauth_token_json,
        "AWS_SSO_ACCOUNT_ID": credentials.aws_sso_account_id,
        "AWS_SSO_ROLE_NAME": credentials.aws_sso_role_name,
        "AZURE_CLIENT_ID": credentials.azure_client_id,
        "AZURE_CLIENT_SECRET": credentials.azure_client_secret,
        "AZURE_TENANT_ID": credentials.azure_tenant_id,
        "AZURE_SUBSCRIPTION_ID": credentials.azure_subscription_id,
        "AZURE_OAUTH_TOKEN_JSON": credentials.azure_oauth_token_json,
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
        env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = str(cfg_path)
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

    # Some workspaces store a GCP external_account (WIF) JSON in the SA key field.
    # Those configs often point at /tmp/launchpad_oidc_token.jwt; rewrite onto a
    # freshly minted workspace-scoped JWT before any CLI uses ADC.
    if "GOOGLE_APPLICATION_CREDENTIALS" not in env:
        sa_blob = env.get("GCP_SA_KEY") or env.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        parsed = _parse_gcp_credential_json(sa_blob)
        if parsed is not None and parsed.get("type") == "external_account":
            cfg_path, _token_path, token = materialize_external_account_credentials(
                parsed,
                org_id=org_id,
                workspace_id=workspace_id,
                env_type=env_type,
            )
            env["GOOGLE_APPLICATION_CREDENTIALS"] = cfg_path
            env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = cfg_path
            env["LAUNCHPAD_OIDC_JWT"] = token
            env.pop("GCP_SA_KEY", None)
            env.pop("GOOGLE_APPLICATION_CREDENTIALS_JSON", None)

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
    gcp_project = project_id_from_gcp_sa_json(env.get("GCP_SA_KEY")) or (
        env.get("GCP_PROJECT_ID") or ""
    ).strip() or None
    if gcp_project:
        env.setdefault("TF_VAR_project_id", gcp_project)
        env.setdefault("GOOGLE_CLOUD_PROJECT", gcp_project)
        env.setdefault("CLOUDSDK_CORE_PROJECT", gcp_project)
        env.setdefault("GCLOUD_PROJECT", gcp_project)

    # Connect OAuth is optional fallback when no SA / WIF ADC was materialized.
    _materialize_interactive_oauth_env(env, workspace_id=workspace_id)
    return env


def _materialize_interactive_oauth_env(env: dict[str, str], *, workspace_id: str) -> None:
    """Turn stored user OAuth token JSON into CLI/SDK env (gcloud ADC, AWS keys, Azure)."""
    settings = get_settings()
    workdir = _oidc_workdir(workspace_id)

    gcp_oauth = env.pop("GCP_OAUTH_TOKEN_JSON", None)
    if gcp_oauth and not env.get("GOOGLE_APPLICATION_CREDENTIALS") and not env.get("GCP_SA_KEY"):
        try:
            from pkg.auth.oauth_loopback.models import CloudTokenSet
            from pkg.auth.oauth_loopback.providers.gcp import gcp_token_has_cloud_platform

            token_set = CloudTokenSet.model_validate_json(gcp_oauth)
            client_id = str(
                (token_set.claims or {}).get("client_id")
                or settings.gcp_oauth_client_id
                or ""
            ).strip()
            client_secret = (settings.gcp_oauth_client_secret or "").strip()
            if token_set.refresh_token and client_id:
                access_token = _mint_gcp_user_access_token(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=token_set.refresh_token,
                )
                if access_token:
                    # gcloud compute needs an access token minted with cloud-platform.
                    # Credential-file ADC alone often yields "insufficient authentication scopes".
                    env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = access_token
                    if not gcp_token_has_cloud_platform(token_set.scope):
                        logger.info(
                            "gcp_oauth_access_token_minted",
                            workspace_id=workspace_id,
                            had_scope_claim=bool(token_set.scope),
                        )
                adc = {
                    "type": "authorized_user",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": token_set.refresh_token,
                }
                adc_path = workdir / "gcp_authorized_user.json"
                adc_path.write_text(json.dumps(adc), encoding="utf-8")
                try:
                    os.chmod(adc_path, 0o600)
                except OSError:
                    pass
                env["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_path)
                env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] = str(adc_path)
            elif token_set.access_token:
                env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token_set.access_token
        except Exception as exc:  # noqa: BLE001
            logger.warning("gcp_oauth_materialize_failed", error=str(exc))

    aws_oauth = env.pop("AWS_OAUTH_TOKEN_JSON", None)
    account_id = env.pop("AWS_SSO_ACCOUNT_ID", None)
    role_name = env.pop("AWS_SSO_ROLE_NAME", None)
    if (
        aws_oauth
        and account_id
        and role_name
        and not env.get("AWS_ACCESS_KEY_ID")
        and not env.get("AWS_ROLE_ARN")
    ):
        try:
            from pkg.auth.oauth_loopback.models import CloudTokenSet

            token_set = CloudTokenSet.model_validate_json(aws_oauth)
            region = str((token_set.claims or {}).get("region") or "us-east-1")
            keys = _aws_sso_get_role_credentials(
                access_token=token_set.access_token,
                account_id=account_id,
                role_name=role_name,
                region=region,
            )
            if keys:
                env["AWS_ACCESS_KEY_ID"] = keys["access_key_id"]
                env["AWS_SECRET_ACCESS_KEY"] = keys["secret_access_key"]
                env["AWS_SESSION_TOKEN"] = keys["session_token"]
                env.setdefault("AWS_DEFAULT_REGION", region)
                env.setdefault("AWS_REGION", region)
        except Exception as exc:  # noqa: BLE001
            logger.warning("aws_oauth_materialize_failed", error=str(exc))

    azure_oauth = env.pop("AZURE_OAUTH_TOKEN_JSON", None)
    if azure_oauth:
        try:
            from pkg.auth.oauth_loopback.models import CloudTokenSet

            token_set = CloudTokenSet.model_validate_json(azure_oauth)
            if token_set.access_token:
                env["AZURE_ACCESS_TOKEN"] = token_set.access_token
            tenant = str((token_set.claims or {}).get("tenant_id") or "").strip()
            if tenant:
                env.setdefault("AZURE_TENANT_ID", tenant)
            client_id = str((token_set.claims or {}).get("client_id") or "").strip()
            if client_id:
                env.setdefault("AZURE_CLIENT_ID", client_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("azure_oauth_materialize_failed", error=str(exc))


def _mint_gcp_user_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str | None:
    """Refresh a user OAuth token with cloud-platform scopes for gcloud compute."""
    from pkg.auth.oauth_loopback.providers.gcp import (
        GCP_CLOUD_PLATFORM_SCOPE,
        GCP_COMPUTE_SCOPE,
        gcp_token_has_cloud_platform,
    )

    scopes = [
        GCP_CLOUD_PLATFORM_SCOPE,
        GCP_COMPUTE_SCOPE,
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
    token: str | None = None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret or None,
            scopes=scopes,
        )
        creds.refresh(Request())
        token = (creds.token or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("gcp_oauth_access_token_refresh_failed", error=str(exc))
        # Fallback: refresh without explicit scopes (uses original grant).
        try:
            import httpx

            data = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
            if client_secret:
                data["client_secret"] = client_secret
            with httpx.Client(timeout=30.0) as client:
                resp = client.post("https://oauth2.googleapis.com/token", data=data)
            if resp.status_code >= 400:
                logger.warning(
                    "gcp_oauth_access_token_fallback_http",
                    status=resp.status_code,
                    detail=resp.text[:200],
                )
                return None
            payload = resp.json()
            token = str(payload.get("access_token") or "").strip() or None
        except Exception as fallback_exc:  # noqa: BLE001
            logger.warning("gcp_oauth_access_token_fallback_failed", error=str(fallback_exc))
            return None

    if not token:
        return None

    # Confirm the minted token actually carries compute scopes.
    try:
        import httpx

        with httpx.Client(timeout=15.0) as client:
            info = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": token},
            )
        if info.status_code < 400:
            granted = str(info.json().get("scope") or "")
            if not gcp_token_has_cloud_platform(granted):
                logger.warning(
                    "gcp_oauth_token_missing_cloud_platform",
                    scope=granted[:200],
                )
                return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("gcp_oauth_tokeninfo_failed", error=str(exc))
    return token


def _aws_sso_get_role_credentials(
    *,
    access_token: str,
    account_id: str,
    role_name: str,
    region: str,
) -> dict[str, str] | None:
    """Exchange an IAM Identity Center access token for temporary AWS keys."""
    import httpx

    url = f"https://portal.sso.{region}.amazonaws.com/federation/credentials"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            url,
            params={"account_id": account_id, "role_name": role_name},
            headers={
                "x-amz-sso_bearer_token": access_token,
                "x-amz-sso-bearer-token": access_token,
            },
        )
    if resp.status_code >= 400:
        return None
    body = resp.json()
    role_creds = body.get("roleCredentials") if isinstance(body, dict) else None
    if not isinstance(role_creds, dict):
        return None
    access_key = str(role_creds.get("accessKeyId") or "").strip()
    secret = str(role_creds.get("secretAccessKey") or "").strip()
    session = str(role_creds.get("sessionToken") or "").strip()
    if not (access_key and secret and session):
        return None
    return {
        "access_key_id": access_key,
        "secret_access_key": secret,
        "session_token": session,
    }

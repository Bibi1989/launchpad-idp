from __future__ import annotations

import json
import os
import structlog
from dataclasses import dataclass
from pathlib import Path

from pkg.auth.oidc.token_engine import OidcTokenEngine, TokenRequest

logger = structlog.get_logger(__name__)

DEFAULT_TOKEN_PATH = "/tmp/launchpad_oidc_token.jwt"
DEFAULT_GCP_CONFIG_PATH = "/tmp/gcp_credential_config.json"


@dataclass
class GcpWifConfig:
    project_number: str
    pool_id: str
    provider_id: str
    target_sa_email: str
    token_file_path: str = DEFAULT_TOKEN_PATH
    credential_config_path: str = DEFAULT_GCP_CONFIG_PATH


@dataclass
class AwsWebIdentityConfig:
    role_arn: str
    role_session_name: str | None = None
    token_file_path: str = DEFAULT_TOKEN_PATH


@dataclass
class InjectionResult:
    env_vars: dict[str, str]
    written_files: list[str]
    token: str


class CredentialInjector:
    """Injects dynamic keyless OIDC credentials into Launchpad execution sandboxes."""

    def __init__(self, token_engine: OidcTokenEngine | None = None) -> None:
        self.token_engine = token_engine or OidcTokenEngine()

    def inject_gcp_wif(
        self,
        *,
        org_id: str,
        workspace_id: str,
        env_type: str = "production",
        wif_config: GcpWifConfig,
        write_files_locally: bool = True,
    ) -> InjectionResult:
        """Inject GCP Workload Identity Federation (WIF) credentials."""
        token_req = TokenRequest(
            org_id=org_id,
            workspace_id=workspace_id,
            env_type=env_type,
            provider="gcp",
            ttl_seconds=900,
        )
        token = self.token_engine.generate_token(token_req)

        # Build GCP credential configuration file
        audience = (
            f"//iam.googleapis.com/projects/{wif_config.project_number}/locations/global/"
            f"workloadIdentityPools/{wif_config.pool_id}/providers/{wif_config.provider_id}"
        )
        service_account_impersonation_url = (
            f"https://iamcredentials.googleapis.com/v1/projects/-/"
            f"serviceAccounts/{wif_config.target_sa_email}:generateAccessToken"
        )

        gcp_config = {
            "type": "external_account",
            "audience": audience,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {
                "file": wif_config.token_file_path,
            },
            "service_account_impersonation_url": service_account_impersonation_url,
        }

        written_files: list[str] = []
        if write_files_locally:
            # Write token file
            t_path = Path(wif_config.token_file_path)
            t_path.parent.mkdir(parents=True, exist_ok=True)
            t_path.write_text(token, encoding="utf-8")
            os.chmod(t_path, 0o600)
            written_files.append(str(t_path))

            # Write GCP config file
            cfg_path = Path(wif_config.credential_config_path)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(gcp_config, indent=2), encoding="utf-8")
            os.chmod(cfg_path, 0o600)
            written_files.append(str(cfg_path))

        env_vars = {
            "GOOGLE_APPLICATION_CREDENTIALS": wif_config.credential_config_path,
            "LAUNCHPAD_OIDC_JWT": token,
        }

        logger.info(
            "gcp_wif_credentials_injected",
            org_id=org_id,
            workspace_id=workspace_id,
            token_path=wif_config.token_file_path,
            config_path=wif_config.credential_config_path,
            target_sa=wif_config.target_sa_email,
        )

        return InjectionResult(
            env_vars=env_vars,
            written_files=written_files,
            token=token,
        )

    def inject_aws_web_identity(
        self,
        *,
        org_id: str,
        workspace_id: str,
        env_type: str = "production",
        aws_config: AwsWebIdentityConfig,
        write_files_locally: bool = True,
    ) -> InjectionResult:
        """Inject AWS IAM Roles with Web Identity credentials."""
        token_req = TokenRequest(
            org_id=org_id,
            workspace_id=workspace_id,
            env_type=env_type,
            provider="aws",
            ttl_seconds=900,
        )
        token = self.token_engine.generate_token(token_req)

        session_name = aws_config.role_session_name or f"launchpad-exec-{workspace_id}"

        written_files: list[str] = []
        if write_files_locally:
            t_path = Path(aws_config.token_file_path)
            t_path.parent.mkdir(parents=True, exist_ok=True)
            t_path.write_text(token, encoding="utf-8")
            os.chmod(t_path, 0o600)
            written_files.append(str(t_path))

        env_vars = {
            "AWS_WEB_IDENTITY_TOKEN_FILE": aws_config.token_file_path,
            "AWS_ROLE_ARN": aws_config.role_arn,
            "AWS_ROLE_SESSION_NAME": session_name,
            "LAUNCHPAD_OIDC_JWT": token,
        }

        logger.info(
            "aws_web_identity_credentials_injected",
            org_id=org_id,
            workspace_id=workspace_id,
            role_arn=aws_config.role_arn,
            role_session_name=session_name,
            token_path=aws_config.token_file_path,
        )

        return InjectionResult(
            env_vars=env_vars,
            written_files=written_files,
            token=token,
        )

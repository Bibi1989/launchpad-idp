from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import jwt

from app.core.secrets import credentials_to_env
from pkg.auth.oidc import GCP_AUDIENCE, reset_key_manager
from pkg.sandbox.exec import (
    AwsWebIdentityConfig,
    CredentialInjector,
    GcpWifConfig,
)


def test_gcp_wif_credential_injection(tmp_path: Path) -> None:
    reset_key_manager()
    injector = CredentialInjector()

    token_file = tmp_path / "launchpad_oidc_token.jwt"
    config_file = tmp_path / "gcp_credential_config.json"

    wif_cfg = GcpWifConfig(
        project_number="1234567890",
        pool_id="launchpad-pool",
        provider_id="launchpad-provider",
        target_sa_email="launchpad-sa@my-gcp-project.iam.gserviceaccount.com",
        token_file_path=str(token_file),
        credential_config_path=str(config_file),
    )

    res = injector.inject_gcp_wif(
        org_id="org-test-1",
        workspace_id="ws-test-1",
        env_type="production",
        wif_config=wif_cfg,
        write_files_locally=True,
    )

    assert token_file.is_file()
    assert config_file.is_file()

    token_content = token_file.read_text(encoding="utf-8")
    assert token_content == res.token

    # Verify GCP credential config JSON structure
    cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert cfg_data["type"] == "external_account"
    assert (
        cfg_data["audience"]
        == "//iam.googleapis.com/projects/1234567890/locations/global/workloadIdentityPools/launchpad-pool/providers/launchpad-provider"
    )
    assert cfg_data["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
    assert cfg_data["token_url"] == "https://sts.googleapis.com/v1/token"
    assert cfg_data["credential_source"]["file"] == str(token_file)
    assert (
        cfg_data["service_account_impersonation_url"]
        == "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/launchpad-sa@my-gcp-project.iam.gserviceaccount.com:generateAccessToken"
    )

    assert res.env_vars["GOOGLE_APPLICATION_CREDENTIALS"] == str(config_file)
    assert res.env_vars["LAUNCHPAD_OIDC_JWT"] == res.token


def test_aws_web_identity_credential_injection(tmp_path: Path) -> None:
    reset_key_manager()
    injector = CredentialInjector()

    token_file = tmp_path / "aws_oidc_token.jwt"

    aws_cfg = AwsWebIdentityConfig(
        role_arn="arn:aws:iam::123456789012:role/LaunchpadExecutionRole",
        role_session_name="custom-session-name",
        token_file_path=str(token_file),
    )

    res = injector.inject_aws_web_identity(
        org_id="org-aws-1",
        workspace_id="ws-aws-1",
        env_type="staging",
        aws_config=aws_cfg,
        write_files_locally=True,
    )

    assert token_file.is_file()
    assert token_file.read_text(encoding="utf-8") == res.token

    assert res.env_vars["AWS_WEB_IDENTITY_TOKEN_FILE"] == str(token_file)
    assert res.env_vars["AWS_ROLE_ARN"] == "arn:aws:iam::123456789012:role/LaunchpadExecutionRole"
    assert res.env_vars["AWS_ROLE_SESSION_NAME"] == "custom-session-name"


def test_credentials_to_env_auto_triggers_keyless_oidc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_key_manager()
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type("S", (), {"launchpad_oidc_issuer_url": "https://oidc.launchpad.test"})(),
    )
    creds_map = {
        "gcp_wif_project_number": "987654321",
        "gcp_wif_pool_id": "my-pool",
        "gcp_wif_provider_id": "my-provider",
        "gcp_wif_target_sa_email": "deployer@proj.iam.gserviceaccount.com",
    }

    env = credentials_to_env(creds_map, org_id="org-auto", workspace_id="ws-auto")
    assert "GOOGLE_APPLICATION_CREDENTIALS" in env
    assert "LAUNCHPAD_OIDC_JWT" in env

    cfg_path = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
    assert cfg_path.is_file()
    cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "my-pool" in cfg_data["audience"]


def test_unwrap_markdown_url() -> None:
    from app.core.secrets import unwrap_markdown_url

    assert (
        unwrap_markdown_url(
            "[https://sts.googleapis.com/v1/token](https://sts.googleapis.com/v1/token)"
        )
        == "https://sts.googleapis.com/v1/token"
    )
    assert unwrap_markdown_url("https://sts.googleapis.com/v1/token") == (
        "https://sts.googleapis.com/v1/token"
    )


def test_credentials_to_env_sanitizes_markdown_token_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_key_manager()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type(
            "S",
            (),
            {
                "launchpad_oidc_issuer_url": "https://oidc.launchpad.test",
                "launchpad_oidc_token_ttl_seconds": 900,
            },
        )(),
    )
    blob = json.dumps(
        {
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/x",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": (
                "[https://sts.googleapis.com/v1/token]"
                "(https://sts.googleapis.com/v1/token)"
            ),
            "credential_source": {"file": "/tmp/launchpad_oidc_token.jwt"},
        }
    )
    env = credentials_to_env({"GCP_SA_KEY": blob}, workspace_id="ws-md-url")
    data = json.loads(Path(env["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8"))
    assert data["token_url"] == "https://sts.googleapis.com/v1/token"


def test_credentials_to_env_prefers_keys_over_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    reset_key_manager()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type(
            "S",
            (),
            {
                "launchpad_oidc_issuer_url": "https://oidc.launchpad.test",
                "gcp_oauth_client_id": "client.apps.googleusercontent.com",
                "gcp_oauth_client_secret": "secret",
            },
        )(),
    )
    monkeypatch.setattr(
        "app.core.secrets._mint_gcp_user_access_token",
        lambda **_kwargs: "ya29.minted",
    )
    oauth = {
        "provider": "gcp",
        "access_token": "ya29.access",
        "refresh_token": "1//refresh",
        "token_type": "Bearer",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "claims": {"client_id": "client.apps.googleusercontent.com"},
    }
    blob = json.dumps(
        {
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/x",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {"file": "/tmp/stale.jwt"},
        }
    )
    env = credentials_to_env(
        {"GCP_SA_KEY": blob, "GCP_OAUTH_TOKEN_JSON": json.dumps(oauth)},
        workspace_id="ws-keys-prefer",
    )
    assert "CLOUDSDK_AUTH_ACCESS_TOKEN" not in env
    data = json.loads(Path(env["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8"))
    assert data["type"] == "external_account"
    assert Path(data["credential_source"]["file"]).is_file()


def test_credentials_to_env_rewrites_external_account_sa_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_key_manager()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type(
            "S",
            (),
            {
                "launchpad_oidc_issuer_url": "https://oidc.launchpad.test",
                "launchpad_oidc_token_ttl_seconds": 900,
            },
        )(),
    )
    blob = json.dumps(
        {
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/x",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {"file": "/tmp/launchpad_oidc_token.jwt"},
            "service_account_impersonation_url": (
                "https://iamcredentials.googleapis.com/v1/projects/-/"
                "serviceAccounts/sa@proj.iam.gserviceaccount.com:generateAccessToken"
            ),
        }
    )
    env = credentials_to_env({"GCP_SA_KEY": blob}, workspace_id="ws-ext-acct")
    assert "GCP_SA_KEY" not in env
    cfg_path = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
    assert cfg_path.is_file()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    token_file = Path(data["credential_source"]["file"])
    assert token_file.is_file()
    assert token_file.name == "oidc_token.jwt"
    assert "/tmp/launchpad_oidc_token.jwt" not in str(token_file)
    assert env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] == str(cfg_path)


def test_credentials_to_env_wif_strips_static_sa_key(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_key_manager()
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type("S", (), {"launchpad_oidc_issuer_url": "https://oidc.launchpad.test"})(),
    )
    env = credentials_to_env(
        {
            "GCP_SA_KEY": '{"type":"service_account","project_id":"ignored"}',
            "GCP_WIF_PROJECT_NUMBER": "111",
            "GCP_WIF_POOL_ID": "pool",
            "GCP_WIF_PROVIDER_ID": "provider",
            "GCP_WIF_TARGET_SA_EMAIL": "sa@proj.iam.gserviceaccount.com",
        },
        workspace_id="ws-wif-prefer",
    )
    assert "GCP_SA_KEY" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" in env
    assert "GCP_WIF_POOL_ID" not in env


def test_credentials_to_env_aws_role_strips_access_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_key_manager()
    monkeypatch.setattr(
        "app.core.secrets.get_settings",
        lambda: type("S", (), {"launchpad_oidc_issuer_url": "https://oidc.launchpad.test"})(),
    )
    env = credentials_to_env(
        {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/Launchpad",
        },
        workspace_id="ws-aws-oidc",
    )
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["AWS_ROLE_ARN"] == "arn:aws:iam::123456789012:role/Launchpad"
    assert "AWS_WEB_IDENTITY_TOKEN_FILE" in env
    assert Path(env["AWS_WEB_IDENTITY_TOKEN_FILE"]).is_file()


def test_has_gcp_and_aws_auth_helpers() -> None:
    from app.core.secrets import has_aws_auth, has_gcp_auth
    from app.schemas.cloud import CloudCredentials

    assert not has_gcp_auth(CloudCredentials())
    assert has_gcp_auth(CloudCredentials(gcp_sa_key_json='{"type":"service_account"}'))
    assert has_gcp_auth(
        CloudCredentials(
            gcp_wif_project_number="1",
            gcp_wif_pool_id="p",
            gcp_wif_provider_id="pr",
            gcp_wif_target_sa_email="a@b.c",
        )
    )
    assert not has_aws_auth(CloudCredentials(aws_access_key_id="AKIA"))
    assert has_aws_auth(
        CloudCredentials(aws_access_key_id="AKIA", aws_secret_access_key="secret")
    )
    assert has_aws_auth(CloudCredentials(aws_role_arn="arn:aws:iam::1:role/x"))


def test_preview_launch_accepts_gcp_wif() -> None:
    from app.schemas.cloud import CloudCredentials
    from app.schemas.environment import PreviewLaunchRequest, PreviewProvider

    payload = PreviewLaunchRequest(
        name="wif-demo",
        template_id="hello-web",
        provider=PreviewProvider.GCP,
        credentials=CloudCredentials(
            gcp_wif_project_number="123456789012",
            gcp_wif_pool_id="launchpad-pool",
            gcp_wif_provider_id="launchpad-provider",
            gcp_wif_target_sa_email="deploy@proj.iam.gserviceaccount.com",
        ),
    )
    assert payload.credentials.gcp_wif_pool_id == "launchpad-pool"
    assert payload.credentials.gcp_sa_key_json is None

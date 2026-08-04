"""Shared cloud-credential validation used by launch + promote request schemas."""

from __future__ import annotations

import pytest

from app.core.secrets import validate_cloud_credentials
from app.schemas.cloud import CloudCredentials


def test_incomplete_credentials_raise_for_each_provider() -> None:
    empty = CloudCredentials()
    for provider in ("gcp", "aws", "azure", "cloudflare"):
        with pytest.raises(ValueError):
            validate_cloud_credentials(provider, empty)


def test_complete_credentials_pass() -> None:
    validate_cloud_credentials(
        "gcp", CloudCredentials(gcp_sa_key_json='{"type": "service_account"}')
    )
    validate_cloud_credentials(
        "aws",
        CloudCredentials(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret"),
    )
    validate_cloud_credentials(
        "azure",
        CloudCredentials(
            azure_client_id="client",
            azure_client_secret="secret",
            azure_tenant_id="tenant",
            azure_subscription_id="sub",
        ),
    )
    validate_cloud_credentials(
        "cloudflare", CloudCredentials(cloudflare_api_token="token")
    )


def test_aws_role_arn_alone_is_sufficient() -> None:
    # Keyless OIDC: a role ARN satisfies AWS auth without access keys.
    validate_cloud_credentials(
        "aws", CloudCredentials(aws_role_arn="arn:aws:iam::123456789012:role/preview")
    )


def test_unknown_or_local_provider_is_a_noop() -> None:
    # Non-cloud/unknown providers are the caller's concern; the validator stays silent.
    validate_cloud_credentials("local", CloudCredentials())
    validate_cloud_credentials("something-else", CloudCredentials())

"""Cloud promote create payload: no TTL + preserved lifecycle stage."""

from __future__ import annotations

from uuid import uuid4

from app.schemas.environment import EnvironmentCreate, PreviewLaunchRequest, PreviewProvider
from app.schemas.cloud import CloudCredentials


def test_environment_create_disable_ttl_skips_default() -> None:
    payload = EnvironmentCreate(
        name="shop-gcp",
        git_branch="main",
        git_repo_url="https://github.com/acme/shop.git",
        disable_ttl=True,
        lifecycle_stage="production",
        promotion_lineage_id=uuid4(),
        promoted_from_id=uuid4(),
    )
    assert payload.disable_ttl is True
    assert payload.ttl_hours is None
    assert payload.ttl_minutes is None
    assert payload.lifecycle_stage == "production"


def test_preview_launch_disable_ttl_for_cloud_promote() -> None:
    lineage = uuid4()
    source = uuid4()
    payload = PreviewLaunchRequest(
        name="shop-gcp",
        git_repo_url="https://github.com/acme/shop.git",
        git_branch="main",
        provider=PreviewProvider.GCP,
        credentials=CloudCredentials(
            gcp_service_account_json=(
                '{"type":"service_account","client_email":"a@b.c",'
                '"private_key":"x","project_id":"p"}'
            ),
        ),
        disable_ttl=True,
        lifecycle_stage="production",
        promotion_lineage_id=lineage,
        promoted_from_id=source,
    )
    assert payload.disable_ttl is True
    assert payload.lifecycle_stage == "production"
    assert payload.promotion_lineage_id == lineage

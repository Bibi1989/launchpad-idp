"""Deploy-to-cloud stage gates (staging/production only)."""

from __future__ import annotations

from app.services.cloud_promote import cloud_promote_stage_allowed


def test_cloud_promote_rejects_preview() -> None:
    assert not cloud_promote_stage_allowed("preview")
    assert not cloud_promote_stage_allowed(None)
    assert not cloud_promote_stage_allowed("")


def test_cloud_promote_allows_staging_and_production() -> None:
    assert cloud_promote_stage_allowed("staging")
    assert cloud_promote_stage_allowed("production")
    assert cloud_promote_stage_allowed("STAGING")

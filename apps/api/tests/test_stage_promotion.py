"""Unit tests for lifecycle stage promotion transitions and policy helpers."""

from __future__ import annotations

from app.models.domain import LifecycleStage
from app.services.promotion import _ALLOWED_TRANSITIONS


def test_allowed_stage_transitions() -> None:
    assert LifecycleStage.STAGING.value in _ALLOWED_TRANSITIONS[LifecycleStage.PREVIEW.value]
    assert LifecycleStage.PRODUCTION.value in _ALLOWED_TRANSITIONS[LifecycleStage.PREVIEW.value]
    assert LifecycleStage.PRODUCTION.value in _ALLOWED_TRANSITIONS[LifecycleStage.STAGING.value]
    assert not _ALLOWED_TRANSITIONS[LifecycleStage.PRODUCTION.value]


def test_production_is_terminal() -> None:
    assert _ALLOWED_TRANSITIONS["production"] == frozenset()

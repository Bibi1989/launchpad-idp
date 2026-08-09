"""Tests for frontend/backend service name classification."""

from app.services.service_kind import is_frontend_app_kind, is_frontend_service_name


def test_api_is_not_frontend_despite_ui_substring() -> None:
    assert is_frontend_service_name("api") is False
    assert is_frontend_service_name("api-server") is False
    assert is_frontend_app_kind(None, name="api") is False


def test_web_and_ui_tokens_are_frontend() -> None:
    assert is_frontend_service_name("web") is True
    assert is_frontend_service_name("web-ui") is True
    assert is_frontend_service_name("frontend") is True
    assert is_frontend_service_name("nextjs") is True


def test_explicit_app_kind_wins() -> None:
    assert is_frontend_app_kind("frontend", name="api") is True
    assert is_frontend_app_kind("backend", name="web") is False

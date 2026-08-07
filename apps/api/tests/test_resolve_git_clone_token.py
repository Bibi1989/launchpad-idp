"""Tests for shared git clone token resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.github_app import GitHubAppAuthError, resolve_git_clone_token


def test_resolve_git_clone_token_prefers_pat() -> None:
    settings = MagicMock()
    settings.github_pat = "ghp_test_pat_value_1234567890"

    with patch("app.services.github_app.is_github_app_configured", return_value=True):
        with patch("app.services.github_app.get_installation_access_token") as mint:
            token = resolve_git_clone_token(settings=settings)
    assert token == "ghp_test_pat_value_1234567890"
    mint.assert_not_called()


def test_resolve_git_clone_token_falls_back_to_app() -> None:
    settings = MagicMock()
    settings.github_pat = None

    with (
        patch("app.services.github_app.is_github_app_configured", return_value=True),
        patch("app.services.github_app.get_installation_access_token", return_value="ghs_app") as mint,
    ):
        token = resolve_git_clone_token(settings=settings, installation_id=42)
    assert token == "ghs_app"
    mint.assert_called_once()


def test_resolve_git_clone_token_strict_app_raises() -> None:
    settings = MagicMock()
    settings.github_pat = None

    with (
        patch("app.services.github_app.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_app.get_installation_access_token",
            side_effect=GitHubAppAuthError("boom"),
        ),
        pytest.raises(GitHubAppAuthError),
    ):
        resolve_git_clone_token(settings=settings, strict_app=True)


def test_resolve_git_clone_token_soft_app_returns_none() -> None:
    settings = MagicMock()
    settings.github_pat = None

    with (
        patch("app.services.github_app.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_app.get_installation_access_token",
            side_effect=GitHubAppAuthError("boom"),
        ),
    ):
        token = resolve_git_clone_token(settings=settings, strict_app=False)
    assert token is None

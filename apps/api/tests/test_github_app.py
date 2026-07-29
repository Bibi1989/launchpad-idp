from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.services.github_app import (
    GitHubAppAuthError,
    clear_github_integration_cache,
    get_github_app_status,
    is_github_app_configured,
    load_github_app_private_key,
    resolve_installation_id,
)


@pytest.fixture
def rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_github_integration_cache()
    yield
    clear_github_integration_cache()


def test_load_private_key_from_pem_string(rsa_pem: str) -> None:
    settings = Settings(github_app_id=12345, github_app_private_key=rsa_pem)
    assert "BEGIN" in load_github_app_private_key(settings)


def test_load_private_key_from_escaped_newlines(rsa_pem: str) -> None:
    escaped = rsa_pem.replace("\n", "\\n")
    settings = Settings(github_app_id=12345, github_app_private_key=escaped)
    loaded = load_github_app_private_key(settings)
    assert "BEGIN PRIVATE KEY" in loaded
    assert "\\n" not in loaded


def test_load_private_key_from_base64(rsa_pem: str) -> None:
    import base64

    encoded = base64.b64encode(rsa_pem.encode("utf-8")).decode("ascii")
    settings = Settings(github_app_id=12345, github_app_private_key=encoded)
    loaded = load_github_app_private_key(settings)
    assert "BEGIN PRIVATE KEY" in loaded
    assert loaded.strip() == rsa_pem.strip()


def test_load_private_key_from_path(tmp_path: Path, rsa_pem: str) -> None:
    key_path = tmp_path / "app.pem"
    key_path.write_text(rsa_pem, encoding="utf-8")
    settings = Settings(
        github_app_id=12345,
        github_app_private_key=None,
        github_app_private_key_path=str(key_path),
    )
    assert load_github_app_private_key(settings).strip() == rsa_pem.strip()


def test_is_configured_false_without_app_id(rsa_pem: str) -> None:
    settings = Settings(github_app_id=None, github_app_private_key=rsa_pem)
    assert is_github_app_configured(settings) is False


def test_status_includes_install_url(rsa_pem: str) -> None:
    settings = Settings(
        github_app_id=42,
        github_app_private_key=rsa_pem,
        github_app_slug="launchpad-idp",
    )
    status = get_github_app_status(settings)
    assert status.configured is True
    assert status.install_url is not None
    assert status.install_url.startswith(
        "https://github.com/apps/launchpad-idp/installations/new"
    )


def test_status_resolves_slug_from_github_when_unset(rsa_pem: str) -> None:
    settings = Settings(
        github_app_id=42,
        github_app_private_key=rsa_pem,
        github_app_slug=None,
    )
    fake_app = MagicMock()
    fake_app.slug = "auto-slug"
    fake_app.raw_data = {"slug": "auto-slug"}
    with patch("app.services.github_app._build_integration") as build:
        build.return_value.get_app.return_value = fake_app
        status = get_github_app_status(settings)
    assert status.configured is True
    assert status.app_slug == "auto-slug"
    assert status.install_url is not None
    assert "/apps/auto-slug/installations/new" in status.install_url


def test_resolve_installation_id_explicit() -> None:
    settings = Settings(github_app_id=1, github_app_installation_id=99)
    assert resolve_installation_id(installation_id=7, settings=settings) == 7


def test_resolve_installation_id_default() -> None:
    settings = Settings(github_app_id=1, github_app_installation_id=55)
    assert resolve_installation_id(settings=settings) == 55


def test_resolve_installation_id_from_org(rsa_pem: str) -> None:
    settings = Settings(github_app_id=1, github_app_private_key=rsa_pem)
    fake_installation = MagicMock()
    fake_installation.id = 88
    with patch("app.services.github_app._build_integration") as build:
        integration = MagicMock()
        integration.get_org_installation.return_value = fake_installation
        build.return_value = integration
        assert (
            resolve_installation_id(organization="acme", settings=settings) == 88
        )
        integration.get_org_installation.assert_called_once_with("acme")


def test_resolve_installation_id_requires_disambiguation(rsa_pem: str) -> None:
    settings = Settings(github_app_id=1, github_app_private_key=rsa_pem)
    with patch("app.services.github_app.list_installations") as listed:
        listed.return_value = [
            MagicMock(id=1),
            MagicMock(id=2),
        ]
        with pytest.raises(GitHubAppAuthError, match="Multiple"):
            resolve_installation_id(settings=settings)


def test_search_all_repositories_filters_by_query() -> None:
    from app.services.github_app import GitHubRepositorySummary, search_all_repositories

    dummy_repos = [
        GitHubRepositorySummary(
            id=101,
            name="launchpad-api",
            full_name="acme/launchpad-api",
            private=True,
            html_url="https://github.com/acme/launchpad-api",
            default_branch="main",
            owner_login="acme",
        ),
        GitHubRepositorySummary(
            id=102,
            name="frontend-web",
            full_name="acme/frontend-web",
            private=False,
            html_url="https://github.com/acme/frontend-web",
            default_branch="main",
            owner_login="acme",
        ),
    ]

    with patch("app.services.github_app.is_github_app_configured", return_value=True):
        with patch("app.services.github_app.list_installations", return_value=[MagicMock(id=1)]):
            with patch("app.services.github_app.list_installation_repositories", return_value=dummy_repos):
                # Search for "api"
                res = search_all_repositories(q="api", installation_id=1)
                assert len(res) == 1
                assert res[0].name == "launchpad-api"

                # Search for "acme" returns both
                res2 = search_all_repositories(q="acme", installation_id=1)
                assert len(res2) == 2


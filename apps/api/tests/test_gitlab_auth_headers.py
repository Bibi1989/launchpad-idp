"""GitLab auth header and OAuth token-type handling."""

from __future__ import annotations

from app.services.gitlab_service import auth_headers


def test_auth_headers_oauth_uses_bearer_only() -> None:
    headers = auth_headers("oauth-token-value", "oauth")
    assert headers["Authorization"] == "Bearer oauth-token-value"
    assert "PRIVATE-TOKEN" not in headers


def test_auth_headers_pat_uses_private_token_only() -> None:
    headers = auth_headers("glpat-example", "pat")
    assert headers["PRIVATE-TOKEN"] == "glpat-example"
    assert "Authorization" not in headers

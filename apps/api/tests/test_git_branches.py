"""Unit tests for GitHub/GitLab branch listing helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.github_app import GitHubAppAuthError, list_repository_branches
from app.services.gitlab_service import GitLabAuthError, GitLabProvisioningService


def test_list_repository_branches_sorts_default_first() -> None:
    default = SimpleNamespace(name="main", protected=True)
    feature = SimpleNamespace(name="feature/x", protected=False)
    repo = MagicMock()
    repo.default_branch = "main"
    repo.get_branches.return_value = [feature, default]
    client = MagicMock()
    client.get_repo.return_value = repo

    with patch(
        "app.services.github_app.get_installation_client",
        return_value=(client, 42, "org", "Organization"),
    ):
        branches = list_repository_branches(
            installation_id=42,
            full_name="org/app",
        )

    assert [b.name for b in branches] == ["main", "feature/x"]
    assert branches[0].is_default is True
    assert branches[1].is_default is False


def test_list_repository_branches_rejects_bad_full_name() -> None:
    with pytest.raises(GitHubAppAuthError, match="owner/repo"):
        list_repository_branches(installation_id=1, full_name="nopath")


def test_gitlab_list_branches_maps_default() -> None:
    svc = GitLabProvisioningService()
    project = {"id": 9, "default_branch": "develop", "path_with_namespace": "g/p"}
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [
        {"name": "feature/a", "protected": False},
        {"name": "develop", "protected": True},
    ]
    client.get.return_value = response

    with (
        patch.object(svc, "_get_project", return_value=project),
        patch("app.services.gitlab_service.httpx.Client") as client_cls,
    ):
        client_cls.return_value.__enter__.return_value = client
        rows = svc.list_branches(
            base_url="https://gitlab.com",
            token="glpat-x",
            project_path="g/p",
            token_type="pat",
        )

    assert [r["name"] for r in rows] == ["develop", "feature/a"]
    assert rows[0]["is_default"] is True


def test_gitlab_list_branches_requires_path() -> None:
    svc = GitLabProvisioningService()
    with pytest.raises(GitLabAuthError, match="project_path"):
        svc.list_branches(
            base_url="https://gitlab.com",
            token="glpat-x",
            project_path="  ",
        )

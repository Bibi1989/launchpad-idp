from __future__ import annotations

from unittest.mock import MagicMock, patch

from github import GithubException

from app.schemas.cloud import CloudProvider, GitHubRepoRequest, IaCEngine
from app.services.github_service import GitHubProvisioningService


def test_create_repo_uses_github_app_auth() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(
        name="launchpad-demo",
        installation_id=42,
        organization="acme",
        set_cloud_secrets=False,
        workspace_id=None,
    )

    fake_repo = MagicMock()
    fake_repo.full_name = "acme/launchpad-demo"
    fake_repo.html_url = "https://github.com/acme/launchpad-demo"
    fake_repo.private = True
    fake_repo.default_branch = "main"
    fake_repo.get_contents.side_effect = GithubException(404, {"message": "Not Found"}, None)

    fake_org = MagicMock()
    fake_org.create_repo.return_value = fake_repo

    fake_client = MagicMock()
    fake_client.get_organization.return_value = fake_org

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_service.get_installation_client",
            return_value=(fake_client, 42, "acme", "Organization"),
        ),
        patch.object(service, "_commit_files") as commit,
        patch.object(
            service,
            "_resolve_bundle",
            return_value=(CloudProvider.GCP, IaCEngine.TERRAFORM, {"main.tf": "resource \"null\" \"x\" {}"}),
        ),
    ):
        result = service.create_repository_with_workflow(request)

    assert result.full_name == "acme/launchpad-demo"
    assert result.installation_id == 42
    assert result.auth_method == "github_app"
    assert result.created is True
    assert result.workflow_path == ".github/workflows/deploy.yml"
    fake_org.create_repo.assert_called_once()
    commit.assert_called_once()
    committed = commit.call_args.args[1]
    assert ".github/workflows/deploy.yml" in committed
    assert "infra/main.tf" in committed


def test_create_repo_requires_app_when_no_pat() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(name="demo", set_cloud_secrets=False)

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=False),
        patch("app.core.config.get_settings") as settings,
    ):
        settings.return_value.github_pat = None
        try:
            service.create_repository_with_workflow(request)
            raised = False
        except ValueError as exc:
            raised = True
            assert "GitHub App is not configured" in str(exc)
    assert raised


def test_create_repo_on_personal_account_reuses_existing() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(
        name="demo",
        installation_id=99,
        set_cloud_secrets=False,
        workspace_id=None,
    )

    fake_repo = MagicMock()
    fake_repo.full_name = "Bibi1989/demo"
    fake_repo.html_url = "https://github.com/Bibi1989/demo"
    fake_repo.private = True
    fake_repo.default_branch = "main"
    fake_repo.get_contents.side_effect = GithubException(404, {"message": "Not Found"}, None)

    fake_client = MagicMock()
    fake_client.get_repo.return_value = fake_repo

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_service.get_installation_client",
            return_value=(fake_client, 99, "Bibi1989", "User"),
        ),
        patch.object(service, "_commit_files"),
        patch.object(
            service,
            "_resolve_bundle",
            return_value=(CloudProvider.GCP, IaCEngine.TERRAFORM, {}),
        ),
    ):
        result = service.create_repository_with_workflow(request)

    assert result.full_name == "Bibi1989/demo"
    assert result.created is False
    fake_client.get_repo.assert_called_once_with("Bibi1989/demo")


def test_create_repo_on_personal_account_missing_repo_raises() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(
        name="missing",
        installation_id=99,
        set_cloud_secrets=False,
    )

    fake_client = MagicMock()
    fake_client.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, None)

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_service.get_installation_client",
            return_value=(fake_client, 99, "Bibi1989", "User"),
        ),
    ):
        try:
            service.create_repository_with_workflow(request)
            raised = False
            message = ""
        except ValueError as exc:
            raised = True
            message = str(exc)

    assert raised
    assert "cannot create new repositories on personal accounts" in message


def test_import_existing_repo_pushes_infra_and_new_workflow_file() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(
        name="demo",
        installation_id=42,
        organization="acme",
        existing_full_name="acme/demo",
        set_cloud_secrets=False,
        include_workflow=True,
        include_dockerfiles=True,
    )

    fake_repo = MagicMock()
    fake_repo.full_name = "acme/demo"
    fake_repo.html_url = "https://github.com/acme/demo"
    fake_repo.private = True
    fake_repo.default_branch = "main"

    def get_contents(path: str) -> MagicMock:
        if path in {".github/workflows/deploy.yml", "README.md"}:
            return MagicMock()
        raise GithubException(404, {"message": "Not Found"}, None)

    fake_repo.get_contents.side_effect = get_contents

    fake_client = MagicMock()
    fake_client.get_repo.return_value = fake_repo

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_service.get_installation_client",
            return_value=(fake_client, 42, "acme", "Organization"),
        ),
        patch.object(service, "_commit_files") as commit,
        patch.object(
            service,
            "_resolve_bundle",
            return_value=(
                CloudProvider.GCP,
                IaCEngine.TERRAFORM,
                {"main.tf": 'resource "null_resource" "x" {}'},
            ),
        ),
    ):
        result = service.create_repository_with_workflow(request)

    assert result.created is False
    assert result.workflow_path == ".github/workflows/launchpad-deploy.yml"
    fake_client.get_repo.assert_called_once_with("acme/demo")
    committed = commit.call_args.args[1]
    assert "infra/main.tf" in committed
    assert ".github/workflows/launchpad-deploy.yml" in committed
    assert "dockers/Dockerfile" in committed
    assert ".github/workflows/deploy.yml" not in committed
    assert "README.md" not in committed


def test_skip_workflow_still_pushes_infra() -> None:
    service = GitHubProvisioningService()
    request = GitHubRepoRequest(
        name="demo",
        installation_id=42,
        organization="acme",
        set_cloud_secrets=False,
        include_workflow=False,
        include_dockerfiles=False,
    )

    fake_repo = MagicMock()
    fake_repo.full_name = "acme/demo"
    fake_repo.html_url = "https://github.com/acme/demo"
    fake_repo.private = True
    fake_repo.default_branch = "main"
    fake_repo.get_contents.side_effect = GithubException(404, {"message": "Not Found"}, None)

    fake_org = MagicMock()
    fake_org.create_repo.return_value = fake_repo
    fake_client = MagicMock()
    fake_client.get_organization.return_value = fake_org

    with (
        patch("app.services.github_service.is_github_app_configured", return_value=True),
        patch(
            "app.services.github_service.get_installation_client",
            return_value=(fake_client, 42, "acme", "Organization"),
        ),
        patch.object(service, "_commit_files") as commit,
        patch.object(
            service,
            "_resolve_bundle",
            return_value=(
                CloudProvider.GCP,
                IaCEngine.TERRAFORM,
                {"main.tf": 'resource "null_resource" "x" {}'},
            ),
        ),
    ):
        result = service.create_repository_with_workflow(request)

    assert result.workflow_path is None
    committed = commit.call_args.args[1]
    assert "infra/main.tf" in committed
    assert not any(path.startswith(".github/workflows/") for path in committed)
    assert "dockers/Dockerfile" not in committed

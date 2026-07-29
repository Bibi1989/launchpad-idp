"""Post PR comments and commit statuses when a preview becomes ready."""

from __future__ import annotations

from dataclasses import dataclass

from github import GithubException

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.domain import Environment
from app.services.git_urls import normalize_git_repo_full_name
from app.services.github_app import (
    GitHubAppAuthError,
    get_installation_client,
    is_github_app_configured,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PreviewPrNotifyResult:
    commented: bool
    status_set: bool
    message: str


def notify_preview_ready(
    environment: Environment,
    *,
    settings: Settings | None = None,
) -> PreviewPrNotifyResult:
    """Comment on the linked PR and set a commit status when GitHub App is configured."""
    cfg = settings or get_settings()
    if environment.github_pr_number is None:
        return PreviewPrNotifyResult(False, False, "no_pr_linked")
    if not is_github_app_configured(cfg):
        return PreviewPrNotifyResult(False, False, "github_app_not_configured")

    full_name = normalize_git_repo_full_name(environment.git_repo_url)
    if full_name is None or "/" not in full_name:
        return PreviewPrNotifyResult(False, False, "invalid_repo")

    owner, repo_name = full_name.split("/", 1)
    app_url = environment.preview_url
    portal_base = cfg.preview_public_base_url.rstrip("/")
    portal_url = f"{portal_base}/p/{environment.id}"
    body = (
        f"### Launchpad preview ready\n\n"
        f"| | |\n|---|---|\n"
        f"| Environment | `{environment.name}` |\n"
        f"| Status | `{environment.status.value}` |\n"
    )
    if app_url:
        body += f"| **Open app** | {app_url} |\n"
    body += f"| Status page | {portal_url} |\n"
    body += (
        f"\nPush to `{environment.git_branch}` rebuilds this preview while it is active.\n"
    )

    try:
        client, _installation_id, _token, _ = get_installation_client(settings=cfg)
        repo = client.get_repo(f"{owner}/{repo_name}")
        pull = repo.get_pull(environment.github_pr_number)
        pull.create_issue_comment(body)
        commented = True

        status_set = False
        sha = environment.latest_commit_sha or pull.head.sha
        if sha and app_url:
            # Commit status (checks:status permission) — lightweight vs Checks API.
            repo.get_commit(sha).create_status(
                state="success",
                target_url=app_url,
                description="Launchpad preview is running",
                context="launchpad/preview",
            )
            status_set = True

        if environment.github_pr_url is None:
            environment.github_pr_url = pull.html_url

        logger.info(
            "preview_pr_notified",
            environment_id=str(environment.id),
            pr=environment.github_pr_number,
            commented=commented,
            status_set=status_set,
        )
        return PreviewPrNotifyResult(commented, status_set, "ok")
    except GitHubAppAuthError as exc:
        logger.warning("preview_pr_notify_auth", error=str(exc))
        return PreviewPrNotifyResult(False, False, str(exc))
    except GithubException as exc:
        logger.warning(
            "preview_pr_notify_failed",
            environment_id=str(environment.id),
            status=exc.status,
            error=str(exc),
        )
        return PreviewPrNotifyResult(False, False, f"github_error:{exc.status}")

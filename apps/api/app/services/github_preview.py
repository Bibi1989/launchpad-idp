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
from app.services.preview_smoke import run_preview_smoke_check
from app.services.preview_urls import (
    portal_environment_url,
    portal_status_url,
    resolve_public_preview_url,
    stable_pr_preview_url,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PreviewPrNotifyResult:
    commented: bool
    status_set: bool
    smoke_ok: bool
    message: str


def notify_preview_ready(
    environment: Environment,
    *,
    settings: Settings | None = None,
) -> PreviewPrNotifyResult:
    """Comment on the linked PR and set a commit status when GitHub App is configured.

    Runs an optional HTTP smoke check against the preview URL before marking the
    status green. Status ``target_url`` points at the Launchpad environment page
    ("Open in Launchpad"); the comment includes app URL + stable PR URL + portal.
    """
    cfg = settings or get_settings()
    if environment.github_pr_number is None:
        return PreviewPrNotifyResult(False, False, True, "no_pr_linked")
    if not is_github_app_configured(cfg):
        return PreviewPrNotifyResult(False, False, True, "github_app_not_configured")

    full_name = normalize_git_repo_full_name(environment.git_repo_url)
    if full_name is None or "/" not in full_name:
        return PreviewPrNotifyResult(False, False, True, "invalid_repo")

    owner, repo_name = full_name.split("/", 1)
    app_url = environment.preview_url
    portal_url = portal_status_url(environment.id, settings=cfg)
    launchpad_url = portal_environment_url(environment.id, settings=cfg)
    pr_stable = stable_pr_preview_url(environment.github_pr_number, settings=cfg)
    public_url = resolve_public_preview_url(
        app_url=app_url,
        pr_number=environment.github_pr_number,
        environment_id=environment.id,
        settings=cfg,
    )

    smoke_target = app_url or pr_stable
    smoke = run_preview_smoke_check(smoke_target, settings=cfg) if smoke_target else (
        type("S", (), {"ok": False, "message": "no_url", "status_code": None})()
    )

    body = (
        f"### Launchpad preview ready\n\n"
        f"| | |\n|---|---|\n"
        f"| Environment | `{environment.name}` |\n"
        f"| Status | `{environment.status.value}` |\n"
        f"| Smoke check | `{'pass' if smoke.ok else 'fail'} ({smoke.message})` |\n"
    )
    if app_url:
        body += f"| **Open app** | {app_url} |\n"
    body += f"| **Stable PR URL** | {pr_stable} |\n"
    body += f"| **Open in Launchpad** | {launchpad_url} |\n"
    body += f"| Status page | {portal_url} |\n"
    body += (
        f"\nPush to `{environment.git_branch}` rebuilds this preview while it is active. "
        f"Closing or merging the PR tears it down automatically.\n"
    )

    try:
        client, _installation_id, _token, _ = get_installation_client(settings=cfg)
        repo = client.get_repo(f"{owner}/{repo_name}")
        pull = repo.get_pull(environment.github_pr_number)
        pull.create_issue_comment(body)
        commented = True

        status_set = False
        sha = environment.latest_commit_sha or pull.head.sha
        if sha:
            state = "success" if smoke.ok else "failure"
            description = (
                "Launchpad preview is running"
                if smoke.ok
                else f"Preview smoke failed: {smoke.message}"[:140]
            )
            repo.get_commit(sha).create_status(
                state=state,
                target_url=launchpad_url,
                description=description,
                context="launchpad/preview",
            )
            # Companion context for the stable/public URL when different from Launchpad deep link.
            if public_url != launchpad_url:
                repo.get_commit(sha).create_status(
                    state=state,
                    target_url=public_url,
                    description=(
                        "Open preview app"
                        if smoke.ok
                        else "Preview URL unreachable"
                    )[:140],
                    context="launchpad/preview-url",
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
            smoke_ok=smoke.ok,
            smoke=smoke.message,
        )
        return PreviewPrNotifyResult(commented, status_set, smoke.ok, "ok" if smoke.ok else smoke.message)
    except GitHubAppAuthError as exc:
        logger.warning("preview_pr_notify_auth", error=str(exc))
        return PreviewPrNotifyResult(False, False, smoke.ok, str(exc))
    except GithubException as exc:
        logger.warning(
            "preview_pr_notify_failed",
            environment_id=str(environment.id),
            status=exc.status,
            error=str(exc),
        )
        return PreviewPrNotifyResult(False, False, smoke.ok, f"github_error:{exc.status}")

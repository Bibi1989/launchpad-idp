from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from github import Auth, Github, GithubException, GithubIntegration

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GitHubInstallationSummary:
    id: int
    account_login: str
    account_type: str
    target_type: str | None
    repository_selection: str | None


@dataclass(frozen=True, slots=True)
class GitHubAppStatus:
    configured: bool
    app_id: int | None
    app_slug: str | None
    install_url: str | None
    default_installation_id: int | None
    message: str


class GitHubAppAuthError(ValueError):
    """Raised when GitHub App credentials are missing or invalid."""


def load_github_app_private_key(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    raw = (cfg.github_app_private_key or "").strip()
    path_value = (cfg.github_app_private_key_path or "").strip()

    if raw:
        pem = _coerce_private_key_pem(raw)
        if pem is not None:
            return pem
        # Treat non-PEM raw values as a path fallback.
        candidate = Path(raw.replace("\\n", "\n")).expanduser()
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
        raise GitHubAppAuthError(
            "GITHUB_APP_PRIVATE_KEY must be a PEM string, base64-encoded PEM, "
            "or an existing file path"
        )

    if path_value:
        candidate = Path(path_value).expanduser()
        if not candidate.is_file():
            raise GitHubAppAuthError(
                f"GITHUB_APP_PRIVATE_KEY_PATH does not exist: {candidate}"
            )
        file_pem = _coerce_private_key_pem(candidate.read_text(encoding="utf-8"))
        if file_pem is None:
            raise GitHubAppAuthError(
                f"GITHUB_APP_PRIVATE_KEY_PATH does not contain a readable PEM: {candidate}"
            )
        return file_pem

    raise GitHubAppAuthError(
        "GitHub App private key missing - set GITHUB_APP_PRIVATE_KEY or "
        "GITHUB_APP_PRIVATE_KEY_PATH"
    )


def _coerce_private_key_pem(value: str) -> str | None:
    """Normalize a PEM, escaped-PEM, or base64-encoded PEM into PEM text."""
    text = value.strip().strip('"').strip("'")
    if not text:
        return None

    normalized = text.replace("\\n", "\n")
    if "BEGIN" in normalized and "PRIVATE KEY" in normalized:
        return normalized if normalized.endswith("\n") else normalized + "\n"

    # Common .env pattern: single-line base64 of the PEM file contents.
    compact = "".join(text.split())
    if len(compact) < 32:
        return None
    try:
        import base64

        decoded = base64.b64decode(compact, validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if "BEGIN" in decoded and "PRIVATE KEY" in decoded:
        return decoded if decoded.endswith("\n") else decoded + "\n"
    return None


def is_github_app_configured(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if not cfg.github_app_id:
        return False
    try:
        load_github_app_private_key(cfg)
    except GitHubAppAuthError:
        return False
    return True


def get_github_app_status(settings: Settings | None = None) -> GitHubAppStatus:
    cfg = settings or get_settings()
    configured = is_github_app_configured(cfg)
    slug = (cfg.github_app_slug or "").strip() or None
    setup_url = (cfg.github_app_setup_url or "http://localhost:3000/integrations/github").rstrip(
        "/"
    )

    # Slug is required for the install URL. Resolve it from GitHub when unset so the
    # Connect button still appears with only APP_ID + private key configured.
    if configured and not slug:
        slug = _resolve_app_slug(cfg)

    install_url = _build_install_url(slug, setup_url) if slug else None

    if configured and install_url:
        message = (
            "GitHub App credentials loaded - click Connect GitHub to authorize an installation"
        )
    elif configured and not install_url:
        message = (
            "GitHub App credentials loaded, but the app slug could not be resolved. "
            "Set GITHUB_APP_SLUG in the API .env (from the app URL: github.com/apps/<slug>)."
        )
    elif not cfg.github_app_id:
        message = "Set GITHUB_APP_ID and a private key on the API to enable GitHub Connect"
    else:
        message = "GITHUB_APP_ID is set but the private key is missing or unreadable"
    return GitHubAppStatus(
        configured=configured,
        app_id=cfg.github_app_id,
        app_slug=slug,
        install_url=install_url,
        default_installation_id=cfg.github_app_installation_id,
        message=message,
    )


def _build_install_url(slug: str, setup_url: str) -> str:
    from urllib.parse import quote

    return (
        f"https://github.com/apps/{slug}/installations/new"
        f"?state={quote(setup_url, safe='')}"
    )


def _resolve_app_slug(settings: Settings) -> str | None:
    """Look up the GitHub App slug via JWT (GET /app) when GITHUB_APP_SLUG is unset."""
    try:
        app = _build_integration(settings).get_app()
    except (GitHubAppAuthError, GithubException, OSError, ValueError) as exc:
        logger.warning(
            "github_app_slug_resolve_failed",
            error=str(exc),
        )
        return None

    slug = getattr(app, "slug", None)
    if isinstance(slug, str) and slug.strip():
        return slug.strip()

    raw = getattr(app, "raw_data", None)
    if isinstance(raw, dict):
        raw_slug = raw.get("slug")
        if isinstance(raw_slug, str) and raw_slug.strip():
            return raw_slug.strip()
        html_url = raw.get("html_url")
        if isinstance(html_url, str) and "/apps/" in html_url:
            return html_url.rstrip("/").rsplit("/apps/", 1)[-1].strip() or None
    return None


def _build_integration(settings: Settings | None = None) -> GithubIntegration:
    cfg = settings or get_settings()
    if not cfg.github_app_id:
        raise GitHubAppAuthError("GITHUB_APP_ID is not configured")
    private_key = load_github_app_private_key(cfg)
    auth = Auth.AppAuth(cfg.github_app_id, private_key)
    return GithubIntegration(auth=auth)


@lru_cache(maxsize=1)
def get_github_integration() -> GithubIntegration:
    return _build_integration()


def clear_github_integration_cache() -> None:
    get_github_integration.cache_clear()


def list_installations(settings: Settings | None = None) -> list[GitHubInstallationSummary]:
    integration = _build_integration(settings)
    summaries: list[GitHubInstallationSummary] = []
    try:
        for installation in integration.get_installations():
            account = installation.raw_data.get("account") or {}
            summaries.append(
                GitHubInstallationSummary(
                    id=installation.id,
                    account_login=str(account.get("login") or installation.id),
                    account_type=str(account.get("type") or "Unknown"),
                    target_type=getattr(installation, "target_type", None),
                    repository_selection=getattr(installation, "repository_selection", None),
                )
            )
    except GithubException as exc:
        logger.error("github_list_installations_failed", status=exc.status)
        raise GitHubAppAuthError("Failed to list GitHub App installations") from exc
    return summaries


def resolve_installation_id(
    *,
    installation_id: int | None = None,
    organization: str | None = None,
    settings: Settings | None = None,
) -> int:
    cfg = settings or get_settings()
    if installation_id is not None:
        return installation_id

    if organization:
        integration = _build_integration(cfg)
        try:
            installation = integration.get_org_installation(organization)
            return int(installation.id)
        except GithubException as exc:
            logger.error(
                "github_org_installation_lookup_failed",
                organization=organization,
                status=exc.status,
            )
            raise GitHubAppAuthError(
                f"No GitHub App installation found for organization '{organization}'. "
                "Install the app on that org, or pass installation_id."
            ) from exc

    if cfg.github_app_installation_id is not None:
        return cfg.github_app_installation_id

    installations = list_installations(cfg)
    if len(installations) == 1:
        return installations[0].id
    if not installations:
        raise GitHubAppAuthError(
            "GitHub App has no installations - install it on an organization first"
        )
    raise GitHubAppAuthError(
        "Multiple GitHub App installations found - pass installation_id or organization"
    )


def get_installation_access_token(
    *,
    installation_id: int | None = None,
    organization: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Mint a short-lived GitHub App installation token for git clone / API calls."""
    resolved_id = resolve_installation_id(
        installation_id=installation_id,
        organization=organization,
        settings=settings,
    )
    integration = _build_integration(settings)
    try:
        access = integration.get_access_token(resolved_id)
        token = getattr(access, "token", None)
        if not token:
            raise GitHubAppAuthError("GitHub App returned an empty installation token")
        return str(token)
    except GithubException as exc:
        logger.error(
            "github_installation_access_token_failed",
            installation_id=resolved_id,
            status=exc.status,
        )
        raise GitHubAppAuthError(
            f"Failed to mint installation access token for installation {resolved_id}"
        ) from exc


def resolve_git_clone_token(
    *,
    settings: Settings | None = None,
    installation_id: int | None = None,
    organization: str | None = None,
    prefer_pat: bool = True,
    allow_github_app: bool = True,
    strict_app: bool = False,
) -> str | None:
    """Resolve a token for git clone / private repo access.

    Prefer ``GITHUB_PAT`` when set (local/dev), then fall back to a GitHub App
    installation token when the App is configured. Returns ``None`` when neither
    source is available (callers treat that as public-repo / no-auth clone).
    """
    cfg = settings or get_settings()
    if prefer_pat:
        pat = (cfg.github_pat or "").strip()
        if pat:
            return pat
    if not allow_github_app:
        return None
    try:
        if not is_github_app_configured(cfg):
            return None
        return get_installation_access_token(
            installation_id=installation_id,
            organization=organization,
            settings=cfg,
        )
    except GitHubAppAuthError as exc:
        if strict_app:
            raise
        logger.warning("git_clone_token_unavailable", error=str(exc))
        return None
    except Exception as exc:
        if strict_app:
            raise
        logger.warning("git_clone_token_unavailable", error=str(exc))
        return None


def get_installation_client(
    *,
    installation_id: int | None = None,
    organization: str | None = None,
    settings: Settings | None = None,
) -> tuple[Github, int, str, str]:
    """Return ``(client, installation_id, account_login, account_type)``.

    ``account_type`` is ``User`` or ``Organization`` from the installation payload.
    """
    resolved_id = resolve_installation_id(
        installation_id=installation_id,
        organization=organization,
        settings=settings,
    )
    integration = _build_integration(settings)
    try:
        installation = integration.get_app_installation(resolved_id)
        account = installation.raw_data.get("account") or {}
        account_login = str(account.get("login") or "")
        account_type = str(account.get("type") or "User")
        client = integration.get_github_for_installation(resolved_id)
    except GithubException as exc:
        logger.error(
            "github_installation_token_failed",
            installation_id=resolved_id,
            status=exc.status,
        )
        raise GitHubAppAuthError(
            f"Failed to mint installation token for installation {resolved_id}"
        ) from exc

    logger.info(
        "github_installation_token_minted",
        installation_id=resolved_id,
        account=account_login or None,
        account_type=account_type,
    )
    return client, resolved_id, account_login, account_type


@dataclass(frozen=True, slots=True)
class GitHubRepositorySummary:
    id: int
    name: str
    full_name: str
    private: bool
    html_url: str
    default_branch: str
    owner_login: str


def list_installation_repositories(
    installation_id: int,
    *,
    settings: Settings | None = None,
    limit: int = 100,
) -> list[GitHubRepositorySummary]:
    """List repositories visible to a GitHub App installation (Vercel-style picker)."""
    resolved_id = resolve_installation_id(
        installation_id=installation_id,
        settings=settings,
    )
    integration = _build_integration(settings)
    repos: list[GitHubRepositorySummary] = []
    capped = max(1, min(limit, 200))
    try:
        installation = integration.get_app_installation(resolved_id)
        for repo in installation.get_repos():
            owner = getattr(repo.owner, "login", None) or str(repo.full_name).split("/")[0]
            repos.append(
                GitHubRepositorySummary(
                    id=int(repo.id),
                    name=str(repo.name),
                    full_name=str(repo.full_name),
                    private=bool(repo.private),
                    html_url=str(repo.html_url),
                    default_branch=str(getattr(repo, "default_branch", None) or "main"),
                    owner_login=str(owner),
                )
            )
            if len(repos) >= capped:
                break
    except GithubException as exc:
        logger.error(
            "github_list_repos_failed",
            installation_id=resolved_id,
            status=exc.status,
        )
        raise GitHubAppAuthError(
            f"Failed to list repositories for installation {resolved_id}"
        ) from exc
    except AttributeError as exc:
        # Older PyGithub without Installation.get_repos - use installation token + REST helper.
        logger.warning("github_list_repos_attr_fallback", error=str(exc))
        client, _, _, _ = get_installation_client(installation_id=resolved_id, settings=settings)
        try:
            raw = client.requester.requestJsonAndCheck(
                "GET",
                "/installation/repositories",
                {"per_page": capped},
            )
            payload = raw[1] if isinstance(raw, tuple) else raw
            items = payload.get("repositories", []) if isinstance(payload, dict) else []
            for item in items[:capped]:
                full_name = str(item.get("full_name") or "")
                owner = str((item.get("owner") or {}).get("login") or full_name.split("/")[0])
                repos.append(
                    GitHubRepositorySummary(
                        id=int(item["id"]),
                        name=str(item.get("name") or full_name.split("/")[-1]),
                        full_name=full_name,
                        private=bool(item.get("private")),
                        html_url=str(item.get("html_url") or ""),
                        default_branch=str(item.get("default_branch") or "main"),
                        owner_login=owner,
                    )
                )
        except Exception as rest_exc:
            logger.error(
                "github_list_repos_rest_failed",
                installation_id=resolved_id,
                error=str(rest_exc),
            )
            raise GitHubAppAuthError(
                f"Failed to list repositories for installation {resolved_id}"
            ) from rest_exc

    repos.sort(key=lambda item: item.full_name.lower())
    return repos


def search_all_repositories(
    *,
    q: str | None = None,
    page: int = 1,
    per_page: int = 100,
    installation_id: int | None = None,
    settings: Settings | None = None,
) -> list[GitHubRepositorySummary]:
    """Fetch user & org repositories across GitHub App installations or PAT, supporting search filter 'q'."""
    cfg = settings or get_settings()
    all_repos: list[GitHubRepositorySummary] = []

    if is_github_app_configured(cfg):
        installations_to_query: list[int] = []
        if installation_id is not None:
            installations_to_query.append(installation_id)
        else:
            try:
                summaries = list_installations(cfg)
                installations_to_query = [s.id for s in summaries]
            except Exception:
                installations_to_query = []

        seen_ids: set[int] = set()
        for inst_id in installations_to_query:
            try:
                inst_repos = list_installation_repositories(inst_id, settings=cfg, limit=200)
                for repo in inst_repos:
                    if repo.id not in seen_ids:
                        seen_ids.add(repo.id)
                        all_repos.append(repo)
            except Exception as exc:
                logger.warning(
                    "github_search_inst_repo_failed",
                    installation_id=inst_id,
                    error=str(exc),
                )

    pat = (cfg.github_pat or "").strip()
    if not all_repos and pat:
        try:
            gh = Github(auth=Auth.Token(pat))
            user = gh.get_user()
            for repo in user.get_repos(type="all", sort="updated"):
                owner = getattr(repo.owner, "login", None) or str(repo.full_name).split("/")[0]
                all_repos.append(
                    GitHubRepositorySummary(
                        id=int(repo.id),
                        name=str(repo.name),
                        full_name=str(repo.full_name),
                        private=bool(repo.private),
                        html_url=str(repo.html_url),
                        default_branch=str(getattr(repo, "default_branch", None) or "main"),
                        owner_login=str(owner),
                    )
                )
                if len(all_repos) >= 200:
                    break
        except Exception as exc:
            logger.warning("github_pat_repo_search_failed", error=str(exc))

    query = (q or "").strip().lower()
    if query:
        all_repos = [
            r
            for r in all_repos
            if query in r.name.lower()
            or query in r.full_name.lower()
            or query in r.owner_login.lower()
        ]

    offset = max(0, (page - 1) * per_page)
    return all_repos[offset : offset + per_page]


@dataclass(frozen=True, slots=True)
class GitHubBranchSummary:
    name: str
    protected: bool
    is_default: bool


def list_repository_branches(
    *,
    installation_id: int,
    full_name: str,
    settings: Settings | None = None,
    limit: int = 100,
) -> list[GitHubBranchSummary]:
    """List branches for a repository visible to a GitHub App installation."""
    repo_name = full_name.strip()
    if "/" not in repo_name:
        raise GitHubAppAuthError("full_name must be owner/repo")
    client, resolved_id, _, _ = get_installation_client(
        installation_id=installation_id,
        settings=settings,
    )
    capped = max(1, min(limit, 200))
    try:
        repo = client.get_repo(repo_name)
        default_branch = str(getattr(repo, "default_branch", None) or "main")
        branches: list[GitHubBranchSummary] = []
        for branch in repo.get_branches():
            name = str(getattr(branch, "name", "") or "")
            if not name:
                continue
            branches.append(
                GitHubBranchSummary(
                    name=name,
                    protected=bool(getattr(branch, "protected", False)),
                    is_default=name == default_branch,
                )
            )
            if len(branches) >= capped:
                break
    except GithubException as exc:
        logger.error(
            "github_list_branches_failed",
            installation_id=resolved_id,
            full_name=repo_name,
            status=exc.status,
        )
        raise GitHubAppAuthError(
            f"Failed to list branches for {repo_name}"
        ) from exc

    branches.sort(key=lambda item: (not item.is_default, item.name.lower()))
    return branches


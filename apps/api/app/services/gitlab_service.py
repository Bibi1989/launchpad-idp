"""GitLab OAuth / PAT auth and project create/push (mirrors workspace files exactly)."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.domain import GitlabConnection, User
from app.services.iac_generator import IaCGenerator

logger = get_logger(__name__)


class GitLabAuthError(ValueError):
    """Raised when GitLab auth or API calls fail."""


def _normalize_base_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        cleaned = f"https://{cleaned}"
    return cleaned


class GitLabAuthService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def oauth_configured(self) -> bool:
        return bool(
            self._settings.gitlab_oauth_client_id
            and self._settings.gitlab_oauth_client_secret
        )

    def authorize_url(self) -> str:
        if not self.oauth_configured():
            raise GitLabAuthError(
                "GitLab OAuth is not configured — set GITLAB_OAUTH_CLIENT_ID and "
                "GITLAB_OAUTH_CLIENT_SECRET"
            )
        base = _normalize_base_url(self._settings.gitlab_base_url)
        state = self._encode_state()
        params = {
            "client_id": self._settings.gitlab_oauth_client_id,
            "redirect_uri": self._settings.gitlab_oauth_redirect_uri,
            "response_type": "code",
            "scope": "api read_user write_repository",
            "state": state,
        }
        return f"{base}/oauth/authorize?{urlencode(params)}"

    def _encode_state(self) -> str:
        now = datetime.now(UTC)
        payload = {
            "typ": "gitlab_oauth_state",
            "nonce": secrets.token_urlsafe(12),
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "iss": "launchpad-idp",
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
        )

    def _verify_state(self, state: str) -> None:
        try:
            payload = jwt.decode(
                state,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
                options={"require": ["exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise GitLabAuthError("Invalid or expired OAuth state") from exc
        if payload.get("typ") != "gitlab_oauth_state":
            raise GitLabAuthError("Invalid OAuth state type")

    async def exchange_code(self, *, code: str, state: str) -> tuple[str, dict[str, Any]]:
        self._verify_state(state)
        if not self.oauth_configured():
            raise GitLabAuthError("GitLab OAuth is not configured")
        base = _normalize_base_url(self._settings.gitlab_base_url)
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_resp = await client.post(
                f"{base}/oauth/token",
                data={
                    "client_id": self._settings.gitlab_oauth_client_id,
                    "client_secret": self._settings.gitlab_oauth_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._settings.gitlab_oauth_redirect_uri,
                },
            )
            if token_resp.status_code >= 400:
                raise GitLabAuthError(
                    f"GitLab token exchange failed ({token_resp.status_code})"
                )
            token_body = token_resp.json()
            access_token = token_body.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                raise GitLabAuthError("GitLab token response missing access_token")
            user = await self._fetch_user(client, base, access_token)
        return access_token, user

    async def validate_pat(
        self,
        *,
        token: str,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        base = _normalize_base_url(base_url or self._settings.gitlab_base_url)
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await self._fetch_user(client, base, token.strip())

    @staticmethod
    async def _fetch_user(
        client: httpx.AsyncClient,
        base: str,
        token: str,
    ) -> dict[str, Any]:
        resp = await client.get(
            f"{base}/api/v4/user",
            headers={"PRIVATE-TOKEN": token, "Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            # Retry with Bearer-only (OAuth tokens)
            resp = await client.get(
                f"{base}/api/v4/user",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code >= 400:
            raise GitLabAuthError(f"GitLab user lookup failed ({resp.status_code})")
        data = resp.json()
        if not isinstance(data, dict) or "username" not in data:
            raise GitLabAuthError("Unexpected GitLab user payload")
        return data

    async def upsert_connection(
        self,
        *,
        owner: User,
        token: str,
        token_type: str,
        base_url: str | None = None,
        username: str,
    ) -> GitlabConnection:
        base = _normalize_base_url(base_url or self._settings.gitlab_base_url)
        existing = await self.get_connection(owner.id)
        encrypted = encrypt_secret(token.strip())
        if existing is None:
            row = GitlabConnection(
                user_id=owner.id,
                base_url=base,
                username=username,
                encrypted_token=encrypted,
                token_type=token_type,
            )
            self._session.add(row)
        else:
            existing.base_url = base
            existing.username = username
            existing.encrypted_token = encrypted
            existing.token_type = token_type
            row = existing
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_connection(self, user_id: UUID) -> GitlabConnection | None:
        try:
            result = await self._session.execute(
                select(GitlabConnection).where(GitlabConnection.user_id == user_id)
            )
            return result.scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 — missing migration must not break GitHub UI
            await self._session.rollback()
            logger.warning("gitlab_connection_lookup_failed", error=str(exc))
            return None

    async def delete_connection(self, owner: User) -> None:
        row = await self.get_connection(owner.id)
        if row is None:
            return
        await self._session.delete(row)
        await self._session.commit()

    def decrypt_token(self, row: GitlabConnection) -> str:
        return decrypt_secret(row.encrypted_token)


class GitLabProvisioningService:
    """Creates GitLab projects and commits workspace files with mirrored paths."""

    def __init__(self, iac_generator: IaCGenerator | None = None) -> None:
        self._iac = iac_generator or IaCGenerator()

    def list_projects(self, *, base_url: str, token: str) -> list[dict[str, Any]]:
        base = _normalize_base_url(base_url)
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{base}/api/v4/projects",
                headers=self._headers(token),
                params={
                    "membership": "true",
                    "simple": "true",
                    "order_by": "last_activity_at",
                    "per_page": 50,
                },
            )
            if resp.status_code >= 400:
                raise GitLabAuthError(f"Failed to list GitLab projects ({resp.status_code})")
            rows = resp.json()
        if not isinstance(rows, list):
            return []
        return [
            {
                "id": int(item["id"]),
                "name": str(item["name"]),
                "path_with_namespace": str(item["path_with_namespace"]),
                "http_url_to_repo": str(item.get("http_url_to_repo") or ""),
                "web_url": str(item.get("web_url") or ""),
                "visibility": str(item.get("visibility") or "private"),
                "default_branch": str(item.get("default_branch") or "main"),
            }
            for item in rows
            if isinstance(item, dict) and "id" in item
        ]

    def create_or_open_project(
        self,
        *,
        base_url: str,
        token: str,
        name: str,
        description: str = "",
        private: bool = True,
        existing_path: str | None = None,
        root_dir: str | None = None,
        include_ci: bool = False,
    ) -> dict[str, Any]:
        base = _normalize_base_url(base_url)
        with httpx.Client(timeout=45.0) as client:
            if existing_path:
                project = self._get_project(client, base, token, existing_path)
                created = False
            else:
                project, created = self._create_project(
                    client,
                    base,
                    token,
                    name=name,
                    description=description,
                    private=private,
                )

            files = self._iac.read_bundle_files(root_dir) if root_dir else {}
            commit_payload = dict(files)
            if include_ci and not any(
                p == ".gitlab-ci.yml" or p.startswith("ci/gitlab/") for p in files
            ):
                commit_payload[".gitlab-ci.yml"] = _default_gitlab_ci(name)

            if commit_payload:
                self._commit_files(
                    client,
                    base,
                    token,
                    project_id=int(project["id"]),
                    branch=str(project.get("default_branch") or "main"),
                    files=commit_payload,
                    message="chore: sync Launchpad workspace files",
                )

        logger.info(
            "gitlab_project_bootstrap",
            path=project.get("path_with_namespace"),
            created=created,
            files=len(commit_payload),
        )
        return {
            "id": int(project["id"]),
            "path_with_namespace": str(project["path_with_namespace"]),
            "web_url": str(project.get("web_url") or ""),
            "http_url_to_repo": str(project.get("http_url_to_repo") or ""),
            "default_branch": str(project.get("default_branch") or "main"),
            "visibility": str(project.get("visibility") or "private"),
            "created": created,
            "files_committed": len(commit_payload),
        }

    def push_workspace_files(
        self,
        *,
        base_url: str,
        token: str,
        project_path: str,
        root_dir: str,
        commit_message: str,
    ) -> dict[str, Any]:
        base = _normalize_base_url(base_url)
        files = self._iac.read_bundle_files(root_dir)
        if not files:
            raise GitLabAuthError("Workspace has no files to push")
        with httpx.Client(timeout=45.0) as client:
            project = self._get_project(client, base, token, project_path)
            self._commit_files(
                client,
                base,
                token,
                project_id=int(project["id"]),
                branch=str(project.get("default_branch") or "main"),
                files=files,
                message=commit_message,
            )
        return {
            "id": int(project["id"]),
            "path_with_namespace": str(project["path_with_namespace"]),
            "web_url": str(project.get("web_url") or ""),
            "http_url_to_repo": str(project.get("http_url_to_repo") or ""),
            "default_branch": str(project.get("default_branch") or "main"),
            "visibility": str(project.get("visibility") or "private"),
            "created": False,
            "files_committed": len(files),
        }

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "PRIVATE-TOKEN": token,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get_project(
        self,
        client: httpx.Client,
        base: str,
        token: str,
        path_with_namespace: str,
    ) -> dict[str, Any]:
        encoded = path_with_namespace.strip().strip("/").replace("/", "%2F")
        resp = client.get(
            f"{base}/api/v4/projects/{encoded}",
            headers=self._headers(token),
        )
        if resp.status_code == 404:
            raise GitLabAuthError(f"GitLab project '{path_with_namespace}' not found")
        if resp.status_code >= 400:
            raise GitLabAuthError(f"Failed to open GitLab project ({resp.status_code})")
        data = resp.json()
        if not isinstance(data, dict):
            raise GitLabAuthError("Unexpected GitLab project payload")
        return data

    def _create_project(
        self,
        client: httpx.Client,
        base: str,
        token: str,
        *,
        name: str,
        description: str,
        private: bool,
    ) -> tuple[dict[str, Any], bool]:
        resp = client.post(
            f"{base}/api/v4/projects",
            headers=self._headers(token),
            json={
                "name": name,
                "path": name.lower().replace(" ", "-"),
                "description": description,
                "visibility": "private" if private else "public",
                "initialize_with_readme": False,
            },
        )
        if resp.status_code == 400 and "has already been taken" in resp.text.lower():
            # Open existing project under the current user namespace.
            user = client.get(f"{base}/api/v4/user", headers=self._headers(token))
            user.raise_for_status()
            username = str(user.json().get("username") or "")
            existing = self._get_project(client, base, token, f"{username}/{name}")
            return existing, False
        if resp.status_code >= 400:
            raise GitLabAuthError(f"Failed to create GitLab project ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise GitLabAuthError("Unexpected create project payload")
        return data, True

    def _commit_files(
        self,
        client: httpx.Client,
        base: str,
        token: str,
        *,
        project_id: int,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> None:
        # Ensure branch exists (empty projects may have no default branch yet).
        self._ensure_branch(client, base, token, project_id=project_id, branch=branch)

        actions: list[dict[str, str]] = []
        for path, content in sorted(files.items()):
            action = "update" if self._file_exists(client, base, token, project_id, branch, path) else "create"
            actions.append(
                {
                    "action": action,
                    "file_path": path,
                    "content": content,
                    "encoding": "text",
                }
            )
        # GitLab commits are capped; batch in chunks of 80.
        for offset in range(0, len(actions), 80):
            chunk = actions[offset : offset + 80]
            resp = client.post(
                f"{base}/api/v4/projects/{project_id}/repository/commits",
                headers=self._headers(token),
                json={
                    "branch": branch,
                    "commit_message": message if offset == 0 else f"{message} (part {offset // 80 + 1})",
                    "actions": chunk,
                },
            )
            if resp.status_code >= 400:
                raise GitLabAuthError(
                    f"GitLab commit failed ({resp.status_code}): {resp.text[:300]}"
                )

    def _ensure_branch(
        self,
        client: httpx.Client,
        base: str,
        token: str,
        *,
        project_id: int,
        branch: str,
    ) -> None:
        resp = client.get(
            f"{base}/api/v4/projects/{project_id}/repository/branches/{branch}",
            headers=self._headers(token),
        )
        if resp.status_code == 200:
            return
        # Create initial commit via README if repo is empty.
        create = client.post(
            f"{base}/api/v4/projects/{project_id}/repository/files/README.md",
            headers=self._headers(token),
            json={
                "branch": branch,
                "content": base64.b64encode(b"# Launchpad workspace\n").decode("ascii"),
                "encoding": "base64",
                "commit_message": "chore: initialize repository",
            },
        )
        if create.status_code >= 400 and create.status_code != 400:
            # 400 often means file already exists — ignore
            logger.warning(
                "gitlab_branch_bootstrap_failed",
                status=create.status_code,
                body=create.text[:200],
            )

    def _file_exists(
        self,
        client: httpx.Client,
        base: str,
        token: str,
        project_id: int,
        branch: str,
        path: str,
    ) -> bool:
        encoded = path.replace("/", "%2F")
        resp = client.get(
            f"{base}/api/v4/projects/{project_id}/repository/files/{encoded}",
            headers=self._headers(token),
            params={"ref": branch},
        )
        return resp.status_code == 200


def _default_gitlab_ci(app_name: str) -> str:
    return (
        f"# Launchpad CI for {app_name}\n"
        "stages:\n"
        "  - test\n"
        "  - build\n"
        "sast:\n"
        "  stage: test\n"
        "  image: returntocorp/semgrep:1.97.0\n"
        "  script:\n"
        "    - semgrep scan --config p/ci --error .\n"
        "build:\n"
        "  stage: build\n"
        "  image: docker:27\n"
        "  services: [docker:27-dind]\n"
        "  script:\n"
        "    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA .\n"
    )


def http_error_from_gitlab(exc: GitLabAuthError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "gitlab_error", "message": str(exc)},
    )

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.core.config import get_settings
from app.schemas.cloud import (
    GitHubAppStatusResponse,
    GitHubInstallationItem,
    GitHubRepoRequest,
    GitHubRepoResult,
    GitHubRepositoryItem,
    GitHubRepositorySearchItem,
    GitHubRepositorySearchResponse,
    GitlabOAuthCallbackRequest,
    GitlabPatConnectRequest,
    GitlabProjectItem,
    GitlabPushRequest,
    GitlabRepoRequest,
    GitlabRepoResult,
    GitlabStatusResponse,
    IaCBundleSummary,
    ProvisioningWizardRequest,
    WorkspaceFileContent,
    WorkspaceFileNode,
    WorkspaceFileWriteRequest,
    WorkspaceFormatRequest,
    WorkspaceFormatResponse,
    WorkspaceListItem,
    WorkspaceMkdirRequest,
    WorkspacePushRequest,
    WorkspaceRenameRequest,
    WorkspaceStarRequest,
    WorkspaceTemplateApplyRequest,
    WorkspaceTemplateInfo,
    WorkspacePromoteRequest,
    WorkspaceWizardConfig,
    GcpApiEnablementResponse,
    ProvisioningCostEstimate,
)
from app.schemas.environment import AuditLogRead
from app.services.audit import AuditService
from app.services.github_app import (
    GitHubAppAuthError,
    get_github_app_status,
    is_github_app_configured,
    list_installation_repositories,
    list_installations,
    search_all_repositories,
)
from app.services.gitlab_service import (
    GitLabAuthError,
    GitLabAuthService,
    http_error_from_gitlab,
)
from app.services.manifest_deploy import (
    inspect_image_exposed_ports,
    resolve_workload_listen_port,
)
from app.services.provisioning import ProvisioningService
from app.services.workspace_file_analyzer import (
    WorkspaceFileAnalyzeRequest,
    WorkspaceFileAnalyzeResponse,
    WorkspaceFileAnalyzerError,
    WorkspaceFileAnalyzerService,
)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


class TerminalSessionResponse(BaseModel):
    session_id: str
    workspace_id: str
    mode: str
    ws_path: str


class ImageInspectRequest(BaseModel):
    image: str = Field(..., min_length=1, max_length=512)


class ImageInspectResponse(BaseModel):
    image: str
    exposed_ports: list[int]
    listen_port: int


def get_provisioning_service(
    session: AsyncSession = Depends(get_db_session),
) -> ProvisioningService:
    return ProvisioningService(session)


def get_audit_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditService:
    return AuditService(session)


class OpenTerminalRequest(BaseModel):
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=40, ge=5, le=200)
    run_init: bool = True


@router.post(
    "/workspaces",
    response_model=IaCBundleSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    payload: ProvisioningWizardRequest,
    user: CurrentUser,
    org: CurrentOrg,
    project_id: UUID | None = Query(default=None),
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.generate_bundle(
        payload,
        owner=user,
        org_id=org.org_id,
        project_id=project_id,
    )


@router.get("/workspaces", response_model=list[WorkspaceListItem])
async def list_workspaces(
    user: CurrentUser,
    org: CurrentOrg,
    starred: bool = Query(default=False),
    project_id: UUID | None = Query(default=None),
    service: ProvisioningService = Depends(get_provisioning_service),
) -> list[WorkspaceListItem]:
    return await service.list_workspaces(
        user,
        org_id=org.org_id,
        starred_only=starred,
        project_id=project_id,
    )


@router.put("/workspaces/{workspace_id}/star", response_model=WorkspaceListItem)
async def set_workspace_starred(
    workspace_id: UUID,
    payload: WorkspaceStarRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceListItem:
    return await service.set_workspace_starred(
        workspace_id, user, starred=payload.starred
    )


@router.get("/workspaces/{workspace_id}", response_model=IaCBundleSummary)
async def get_workspace(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.get_bundle_summary(workspace_id, user)


@router.get("/workspaces/{workspace_id}/audits", response_model=list[AuditLogRead])
async def list_workspace_audits(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
    audits: AuditService = Depends(get_audit_service),
    limit: int = 50,
) -> list[AuditLogRead]:
    await service.get_workspace_for_owner(workspace_id, user)
    rows = await audits.list_for_workspace(workspace_id, limit=limit)
    return [AuditLogRead.model_validate(row) for row in rows]


@router.get("/workspaces/{workspace_id}/config", response_model=WorkspaceWizardConfig)
async def get_workspace_wizard_config(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceWizardConfig:
    return await service.get_wizard_config(workspace_id, user)


@router.post(
    "/workspaces/{workspace_id}/promote",
    response_model=IaCBundleSummary,
    status_code=status.HTTP_201_CREATED,
)
async def promote_workspace(
    workspace_id: UUID,
    payload: WorkspacePromoteRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.promote_workspace(
        workspace_id,
        payload,
        owner=user,
        org_id=org.org_id,
    )


@router.post("/estimate-cost", response_model=ProvisioningCostEstimate)
async def estimate_provisioning_cost(
    payload: ProvisioningWizardRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> ProvisioningCostEstimate:
    _ = user
    return service.estimate_workspace_cost(payload)


@router.post(
    "/workspaces/{workspace_id}/enable-cloud-apis",
    response_model=GcpApiEnablementResponse,
)
async def enable_workspace_cloud_apis(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> GcpApiEnablementResponse:
    """Enable required cloud APIs (GCP) before Terraform provision."""
    return await service.enable_required_cloud_apis(workspace_id, user)


@router.put("/workspaces/{workspace_id}", response_model=IaCBundleSummary)
async def update_workspace(
    workspace_id: UUID,
    payload: ProvisioningWizardRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.update_workspace(workspace_id, payload, owner=user)


@router.post(
    "/workspaces/{workspace_id}/restore-files",
    response_model=IaCBundleSummary,
)
async def restore_workspace_files(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    """Recreate missing on-disk IaC/manifest files from the persisted wizard snapshot."""
    return await service.restore_workspace_files(workspace_id, user)


@router.delete(
    "/workspaces/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def destroy_workspace(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> Response:
    await service.destroy_workspace(workspace_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/terminal",
    response_model=TerminalSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_terminal(
    workspace_id: UUID,
    payload: OpenTerminalRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> TerminalSessionResponse:
    session = await service.open_terminal(
        workspace_id,
        owner=user,
        cols=payload.cols,
        rows=payload.rows,
        run_init=payload.run_init,
    )
    return TerminalSessionResponse(
        session_id=session.session_id,
        workspace_id=str(workspace_id),
        mode=session.mode,
        ws_path=f"/api/v1/ws/terminal/{session.session_id}",
    )


@router.get(
    "/workspaces/{workspace_id}/files/tree",
    response_model=list[WorkspaceFileNode],
)
async def list_workspace_file_tree(
    workspace_id: UUID,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> list[WorkspaceFileNode]:
    return await service.list_workspace_files(workspace_id, user)


@router.get(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceFileContent,
)
async def read_workspace_file(
    workspace_id: UUID,
    path: str,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileContent:
    return await service.read_workspace_file(workspace_id, user, path)


@router.put(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceFileContent,
)
async def write_workspace_file(
    workspace_id: UUID,
    payload: WorkspaceFileWriteRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileContent:
    return await service.write_workspace_file(
        workspace_id,
        user,
        relative_path=payload.path,
        content=payload.content,
    )


@router.post(
    "/workspaces/{workspace_id}/files/mkdir",
    response_model=WorkspaceFileNode,
    status_code=status.HTTP_201_CREATED,
)
async def mkdir_workspace_path(
    workspace_id: UUID,
    payload: WorkspaceMkdirRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileNode:
    return await service.mkdir_workspace(workspace_id, user, payload.path)


@router.post(
    "/workspaces/{workspace_id}/files/rename",
    response_model=WorkspaceFileNode,
)
async def rename_workspace_path(
    workspace_id: UUID,
    payload: WorkspaceRenameRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileNode:
    return await service.rename_workspace_path(
        workspace_id,
        user,
        from_path=payload.from_path,
        to_path=payload.to_path,
    )


@router.delete(
    "/workspaces/{workspace_id}/files",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_workspace_path(
    workspace_id: UUID,
    path: str,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> Response:
    await service.delete_workspace_path(workspace_id, user, path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/files/format",
    response_model=WorkspaceFormatResponse,
)
async def format_workspace_file(
    workspace_id: UUID,
    payload: WorkspaceFormatRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFormatResponse:
    return await service.format_workspace_content(
        workspace_id,
        user,
        relative_path=payload.path,
        content=payload.content,
    )


@router.get("/templates", response_model=list[WorkspaceTemplateInfo])
async def list_workspace_templates(
    user: CurrentUser,
    category: str | None = None,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> list[WorkspaceTemplateInfo]:
    del user
    return service.list_file_templates(category)


@router.post(
    "/workspaces/{workspace_id}/files/from-template",
    response_model=WorkspaceFileContent,
    status_code=status.HTTP_201_CREATED,
)
async def apply_workspace_template(
    workspace_id: UUID,
    payload: WorkspaceTemplateApplyRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileContent:
    return await service.apply_file_template(workspace_id, user, payload)


@router.post(
    "/workspaces/{workspace_id}/github/push",
    response_model=GitHubRepoResult,
)
async def push_workspace_github(
    workspace_id: UUID,
    payload: WorkspacePushRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> GitHubRepoResult:
    return await service.push_workspace_to_github(workspace_id, user, payload)


@router.post(
    "/workspaces/{workspace_id}/gitlab/push",
    response_model=GitlabRepoResult,
)
async def push_workspace_gitlab(
    workspace_id: UUID,
    payload: GitlabPushRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> GitlabRepoResult:
    return await service.push_workspace_to_gitlab(workspace_id, user, payload)


@router.get("/gitlab/status", response_model=GitlabStatusResponse)
async def gitlab_status(
    user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> GitlabStatusResponse:
    settings = get_settings()
    auth = GitLabAuthService(session, settings)
    row = await auth.get_connection(user.id)
    authorize_url: str | None = None
    if auth.oauth_configured():
        try:
            authorize_url = auth.authorize_url()
        except GitLabAuthError:
            authorize_url = None
    if row is None:
        return GitlabStatusResponse(
            connected=False,
            oauth_configured=auth.oauth_configured(),
            authorize_url=authorize_url,
            base_url=settings.gitlab_base_url.rstrip("/"),
            username=None,
            token_type=None,
            message=(
                "Connect GitLab with OAuth or a Personal Access Token "
                "(api + write_repository scopes)."
            ),
        )
    return GitlabStatusResponse(
        connected=True,
        oauth_configured=auth.oauth_configured(),
        authorize_url=authorize_url,
        base_url=row.base_url,
        username=row.username,
        token_type=row.token_type,
        message=f"Connected as {row.username}",
    )


@router.post("/gitlab/connect/pat", response_model=GitlabStatusResponse)
async def gitlab_connect_pat(
    payload: GitlabPatConnectRequest,
    user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> GitlabStatusResponse:
    auth = GitLabAuthService(session)
    try:
        profile = await auth.validate_pat(token=payload.token, base_url=payload.base_url)
        await auth.upsert_connection(
            owner=user,
            token=payload.token,
            token_type="pat",
            base_url=payload.base_url,
            username=str(profile.get("username") or profile.get("name") or "gitlab"),
        )
    except GitLabAuthError as exc:
        raise http_error_from_gitlab(exc) from exc
    return await gitlab_status(user=user, session=session)


@router.post("/gitlab/oauth/callback", response_model=GitlabStatusResponse)
async def gitlab_oauth_callback(
    payload: GitlabOAuthCallbackRequest,
    user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> GitlabStatusResponse:
    auth = GitLabAuthService(session)
    try:
        token, profile, refresh_token, expires_at = await auth.exchange_code(
            code=payload.code, state=payload.state
        )
        await auth.upsert_connection(
            owner=user,
            token=token,
            token_type="oauth",
            username=str(profile.get("username") or profile.get("name") or "gitlab"),
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
    except GitLabAuthError as exc:
        raise http_error_from_gitlab(exc) from exc
    return await gitlab_status(user=user, session=session)


@router.delete("/gitlab/connection", status_code=status.HTTP_204_NO_CONTENT)
async def gitlab_disconnect(
    user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    auth = GitLabAuthService(session)
    await auth.delete_connection(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/gitlab/projects", response_model=list[GitlabProjectItem])
async def gitlab_list_projects(
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
    q: str | None = Query(default=None, max_length=200),
) -> list[GitlabProjectItem]:
    return await service.list_gitlab_projects(owner=user, search=q)


@router.post(
    "/gitlab/repositories",
    response_model=GitlabRepoResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_gitlab_repository(
    payload: GitlabRepoRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> GitlabRepoResult:
    return await service.create_gitlab_repo(payload, owner=user)


@router.post(
    "/workspaces/{workspace_id}/analyze-file",
    response_model=WorkspaceFileAnalyzeResponse,
)
async def analyze_workspace_file(
    workspace_id: UUID,
    payload: WorkspaceFileAnalyzeRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> WorkspaceFileAnalyzeResponse:
    """AI/heuristic review for CI/CD, Docker, IaC, or Kubernetes workspace files."""
    await service.get_workspace_for_owner(workspace_id, user)
    analyzer = WorkspaceFileAnalyzerService()
    try:
        return await analyzer.analyze(
            path=payload.path,
            content=payload.content,
            kind=payload.kind,
            error_context=payload.error_context,
            correlation_id=str(workspace_id),
        )
    except WorkspaceFileAnalyzerError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "analyze_failed", "message": str(exc)},
        ) from exc


@router.get("/github/status", response_model=GitHubAppStatusResponse)
async def github_app_status(user: CurrentUser) -> GitHubAppStatusResponse:
    status_payload = get_github_app_status()
    installations: list[GitHubInstallationItem] = []
    if status_payload.configured:
        try:
            installations = [
                GitHubInstallationItem(
                    id=item.id,
                    account_login=item.account_login,
                    account_type=item.account_type,
                    target_type=item.target_type,
                    repository_selection=item.repository_selection,
                )
                for item in list_installations()
            ]
        except GitHubAppAuthError:
            # Status still useful even if listing fails (bad key, network, etc.).
            installations = []
    return GitHubAppStatusResponse(
        configured=status_payload.configured,
        app_id=status_payload.app_id,
        app_slug=status_payload.app_slug,
        install_url=status_payload.install_url,
        default_installation_id=status_payload.default_installation_id,
        message=status_payload.message,
        installations=installations,
    )


@router.get("/github/installations", response_model=list[GitHubInstallationItem])
async def github_installations(user: CurrentUser) -> list[GitHubInstallationItem]:
    if not is_github_app_configured():
        return []
    try:
        return [
            GitHubInstallationItem(
                id=item.id,
                account_login=item.account_login,
                account_type=item.account_type,
                target_type=item.target_type,
                repository_selection=item.repository_selection,
            )
            for item in list_installations()
        ]
    except GitHubAppAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "github_app_error", "message": str(exc)},
        ) from exc


@router.get(
    "/github/installations/{installation_id}/repositories",
    response_model=list[GitHubRepositoryItem],
)
async def github_installation_repositories(
    installation_id: int,
    user: CurrentUser,
) -> list[GitHubRepositoryItem]:
    if not is_github_app_configured():
        return []
    try:
        return [
            GitHubRepositoryItem(
                id=item.id,
                name=item.name,
                full_name=item.full_name,
                private=item.private,
                html_url=item.html_url,
                default_branch=item.default_branch,
                owner_login=item.owner_login,
            )
            for item in list_installation_repositories(installation_id)
        ]
    except GitHubAppAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "github_app_error", "message": str(exc)},
        ) from exc


@router.get(
    "/github/repositories",
    response_model=GitHubRepositorySearchResponse,
)
async def github_search_repositories(
    user: CurrentUser,
    q: str | None = None,
    page: int = 1,
    per_page: int = 100,
    installation_id: int | None = None,
) -> GitHubRepositorySearchResponse:
    _ = user
    try:
        items = search_all_repositories(
            q=q,
            page=page,
            per_page=per_page,
            installation_id=installation_id,
        )
        return GitHubRepositorySearchResponse(
            repositories=[
                GitHubRepositorySearchItem(
                    id=item.id,
                    name=item.name,
                    fullName=item.full_name,
                    isPrivate=item.private,
                    owner=item.owner_login,
                    defaultBranch=item.default_branch,
                    htmlUrl=item.html_url,
                )
                for item in items
            ]
        )
    except GitHubAppAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "github_app_error", "message": str(exc)},
        ) from exc


@router.post(
    "/github/repositories",
    response_model=GitHubRepoResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_github_repository(
    payload: GitHubRepoRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> GitHubRepoResult:
    return await service.create_github_repo(payload, owner=user)


@router.post("/images/inspect", response_model=ImageInspectResponse)
async def inspect_container_image(
    payload: ImageInspectRequest,
    user: CurrentUser,
) -> ImageInspectResponse:
    """Inspect docker image EXPOSE ports for containerPort / Service targetPort prefill."""
    _ = user
    image = payload.image.strip()
    if not image:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_image", "message": "image is required"},
        )
    exposed = await asyncio.to_thread(inspect_image_exposed_ports, image)
    listen_port = resolve_workload_listen_port(
        image=image,
        manifest_port=None,
        exposed_ports=exposed,
    )
    return ImageInspectResponse(
        image=image,
        exposed_ports=exposed,
        listen_port=listen_port,
    )

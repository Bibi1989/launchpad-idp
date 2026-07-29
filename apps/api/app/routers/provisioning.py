from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.cloud import (
    GitHubAppStatusResponse,
    GitHubInstallationItem,
    GitHubRepoRequest,
    GitHubRepoResult,
    GitHubRepositoryItem,
    GitHubRepositorySearchItem,
    GitHubRepositorySearchResponse,
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
    WorkspaceTemplateApplyRequest,
    WorkspaceTemplateInfo,
    WorkspaceWizardConfig,
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
from app.services.manifest_deploy import (
    inspect_image_exposed_ports,
    resolve_workload_listen_port,
)
from app.services.provisioning import ProvisioningService

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
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.generate_bundle(payload, owner=user, org_id=org.org_id)


@router.get("/workspaces", response_model=list[WorkspaceListItem])
async def list_workspaces(
    user: CurrentUser,
    org: CurrentOrg,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> list[WorkspaceListItem]:
    return await service.list_workspaces(user, org_id=org.org_id)


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


@router.put("/workspaces/{workspace_id}", response_model=IaCBundleSummary)
async def update_workspace(
    workspace_id: UUID,
    payload: ProvisioningWizardRequest,
    user: CurrentUser,
    service: ProvisioningService = Depends(get_provisioning_service),
) -> IaCBundleSummary:
    return await service.update_workspace(workspace_id, payload, owner=user)


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

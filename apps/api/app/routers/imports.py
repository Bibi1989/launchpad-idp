"""Repository import API: clone → detect → save as workspace."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.repo_import import (
    RepoImportCreateRequest,
    RepoImportSaveRequest,
    RepoImportSaveResult,
    RepoImportSessionRead,
)
from app.services.repo_import import RepoImportService

router = APIRouter(prefix="/imports", tags=["imports"])


def get_repo_import_service(
    session: AsyncSession = Depends(get_db_session),
) -> RepoImportService:
    return RepoImportService(session)


@router.post("", response_model=RepoImportSessionRead, status_code=status.HTTP_201_CREATED)
async def create_import(
    body: RepoImportCreateRequest,
    user: CurrentUser,
    service: RepoImportService = Depends(get_repo_import_service),
) -> RepoImportSessionRead:
    return await service.start_import(body, owner=user)


@router.get("/{import_id}", response_model=RepoImportSessionRead)
async def get_import(
    import_id: str,
    user: CurrentUser,
    service: RepoImportService = Depends(get_repo_import_service),
) -> RepoImportSessionRead:
    return await service.get_import(import_id, owner=user)


@router.post("/{import_id}/save", response_model=RepoImportSaveResult)
async def save_import(
    import_id: str,
    body: RepoImportSaveRequest,
    user: CurrentUser,
    org: CurrentOrg,
    service: RepoImportService = Depends(get_repo_import_service),
) -> RepoImportSaveResult:
    return await service.save_as_workspace(
        import_id,
        body,
        owner=user,
        org_id=org.org_id,
    )


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_import(
    import_id: str,
    user: CurrentUser,
    service: RepoImportService = Depends(get_repo_import_service),
) -> Response:
    await service.discard(import_id, owner=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Account cloud credential vault API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.schemas.cloud_oauth import (
    CloudOAuthCapabilities,
    CloudOAuthSessionStatus,
    CloudOAuthStartRequest,
)
from app.schemas.user_credentials import (
    UserCloudCredentialsStatus,
    UserCloudCredentialsUpdate,
)
from app.services.cloud_oauth import CloudOAuthError, CloudOAuthService
from app.services.user_credentials import UserCloudCredentialsService

router = APIRouter(prefix="/users/me/cloud-credentials", tags=["user-credentials"])


def get_user_credentials_service(
    session: AsyncSession = Depends(get_db_session),
) -> UserCloudCredentialsService:
    return UserCloudCredentialsService(session)


def get_cloud_oauth_service(
    session: AsyncSession = Depends(get_db_session),
) -> CloudOAuthService:
    return CloudOAuthService(session)


@router.get("", response_model=UserCloudCredentialsStatus)
async def get_cloud_credentials_status(
    user: CurrentUser,
    service: UserCloudCredentialsService = Depends(get_user_credentials_service),
) -> UserCloudCredentialsStatus:
    return await service.get_status(user.id)


@router.put("", response_model=UserCloudCredentialsStatus)
async def upsert_cloud_credentials(
    payload: UserCloudCredentialsUpdate,
    user: CurrentUser,
    service: UserCloudCredentialsService = Depends(get_user_credentials_service),
) -> UserCloudCredentialsStatus:
    return await service.upsert(user.id, payload)


@router.delete("", response_model=UserCloudCredentialsStatus, status_code=status.HTTP_200_OK)
async def clear_cloud_credentials(
    user: CurrentUser,
    service: UserCloudCredentialsService = Depends(get_user_credentials_service),
) -> UserCloudCredentialsStatus:
    return await service.clear_all(user.id)


@router.get("/oauth/capabilities", response_model=CloudOAuthCapabilities)
async def cloud_oauth_capabilities(
    user: CurrentUser,
    service: CloudOAuthService = Depends(get_cloud_oauth_service),
) -> CloudOAuthCapabilities:
    _ = user
    return service.capabilities()


@router.post("/oauth/start", response_model=CloudOAuthSessionStatus)
async def cloud_oauth_start(
    payload: CloudOAuthStartRequest,
    user: CurrentUser,
    service: CloudOAuthService = Depends(get_cloud_oauth_service),
) -> CloudOAuthSessionStatus:
    try:
        return await service.start(user.id, payload)
    except CloudOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/oauth/sessions/{session_id}", response_model=CloudOAuthSessionStatus)
async def cloud_oauth_session_status(
    session_id: str,
    user: CurrentUser,
    service: CloudOAuthService = Depends(get_cloud_oauth_service),
) -> CloudOAuthSessionStatus:
    try:
        return await service.get_session(user.id, session_id)
    except CloudOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

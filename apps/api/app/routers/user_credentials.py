"""Account cloud credential vault API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.schemas.user_credentials import (
    UserCloudCredentialsStatus,
    UserCloudCredentialsUpdate,
)
from app.services.user_credentials import UserCloudCredentialsService

router = APIRouter(prefix="/users/me/cloud-credentials", tags=["user-credentials"])


def get_user_credentials_service(
    session: AsyncSession = Depends(get_db_session),
) -> UserCloudCredentialsService:
    return UserCloudCredentialsService(session)


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

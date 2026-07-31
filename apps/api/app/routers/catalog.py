from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.deps.org import CurrentOrg
from app.schemas.catalog import (
    CatalogServiceCreate,
    CatalogServiceRead,
    CatalogServiceUpdate,
    GoldenPathTemplateRead,
)
from app.services.catalog import CatalogServiceManager

router = APIRouter(prefix="/catalog", tags=["catalog"])


def get_catalog_service(session: AsyncSession = Depends(get_db_session)) -> CatalogServiceManager:
    return CatalogServiceManager(session)


@router.get("/templates", response_model=list[GoldenPathTemplateRead])
async def list_golden_path_templates(
    user: CurrentUser,
    service: CatalogServiceManager = Depends(get_catalog_service),
) -> list[GoldenPathTemplateRead]:
    _ = user
    return service.list_templates()


@router.get("/services", response_model=list[CatalogServiceRead])
async def list_catalog_services(
    user: CurrentUser,
    org: CurrentOrg,
    service: CatalogServiceManager = Depends(get_catalog_service),
) -> list[CatalogServiceRead]:
    return await service.list_services(owner=user, org_id=org.org_id)


@router.get("/services/{service_id}", response_model=CatalogServiceRead)
async def get_catalog_service_detail(
    service_id: UUID,
    user: CurrentUser,
    org: CurrentOrg,
    service: CatalogServiceManager = Depends(get_catalog_service),
) -> CatalogServiceRead:
    return await service.get_service(service_id, owner=user, org_id=org.org_id)


@router.post(
    "/services",
    response_model=CatalogServiceRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_catalog_service(
    payload: CatalogServiceCreate,
    user: CurrentUser,
    org: CurrentOrg,
    service: CatalogServiceManager = Depends(get_catalog_service),
) -> CatalogServiceRead:
    return await service.create_service(payload, owner=user, org_id=org.org_id)


@router.patch("/services/{service_id}", response_model=CatalogServiceRead)
async def update_catalog_service(
    service_id: UUID,
    payload: CatalogServiceUpdate,
    user: CurrentUser,
    org: CurrentOrg,
    service: CatalogServiceManager = Depends(get_catalog_service),
) -> CatalogServiceRead:
    return await service.update_service(service_id, payload, owner=user, org_id=org.org_id)

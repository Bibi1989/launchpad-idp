from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.deps.auth import CurrentUser
from app.schemas.auth import (
    AuthConfigResponse,
    MeResponse,
    OidcCallbackRequest,
    OidcStartResponse,
    OrgSummary,
    TokenResponse,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.services.auth import AuthService
from app.services.oidc import OidcService
from app.services.orgs import OrganizationService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(session: AsyncSession = Depends(get_db_session)) -> AuthService:
    return AuthService(session)


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    settings = get_settings()
    return AuthConfigResponse(
        dev_login_enabled=settings.auth_dev_login_enabled,
        oidc_enabled=settings.oidc_enabled,
        oidc_provider_name=settings.oidc_provider_name if settings.oidc_enabled else None,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: UserRegister,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await service.register(payload)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: UserLogin,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await service.login(payload)


@router.post("/dev-login", response_model=TokenResponse)
async def dev_login(
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await service.dev_login()


@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser,
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    orgs_service = OrganizationService(session)
    rows = await orgs_service.list_for_user(user)
    await session.commit()
    active = str(rows[0][0].id) if rows else None
    return MeResponse(
        user=UserRead.model_validate(user),
        orgs=[
            OrgSummary(
                id=str(org.id),
                slug=org.slug,
                name=org.name,
                role=role.value,
            )
            for org, role in rows
        ],
        active_org_id=active,
        needs_org_setup=len(rows) == 0,
    )


@router.get("/oidc/start", response_model=OidcStartResponse)
async def oidc_start() -> OidcStartResponse:
    service = OidcService()
    url, state = await service.authorization_url()
    return OidcStartResponse(authorization_url=url, state=state)


@router.post("/oidc/callback", response_model=TokenResponse)
async def oidc_callback(
    payload: OidcCallbackRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    oidc = OidcService()
    claims = await oidc.exchange_code(code=payload.code, state=payload.state)
    return await service.upsert_oidc_user(
        issuer=claims.issuer,
        subject=claims.subject,
        email=claims.email,
        display_name=claims.display_name,
        groups=list(claims.groups),
    )

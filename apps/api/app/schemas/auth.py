from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class AuthConfigResponse(BaseModel):
    dev_login_enabled: bool
    oidc_enabled: bool = False
    oidc_provider_name: str | None = None


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name is required")
        return cleaned


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class OrgSummary(BaseModel):
    id: str
    slug: str
    name: str
    role: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
    orgs: list[OrgSummary] = Field(default_factory=list)
    active_org_id: str | None = None
    needs_org_setup: bool = False


class MeResponse(BaseModel):
    user: UserRead
    orgs: list[OrgSummary] = Field(default_factory=list)
    active_org_id: str | None = None
    needs_org_setup: bool = False


class OidcStartResponse(BaseModel):
    authorization_url: str
    state: str


class OidcCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)

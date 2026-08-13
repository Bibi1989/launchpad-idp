"""Run interactive cloud OAuth (loopback) and persist tokens in the user vault.

Loopback listeners run in a background thread on the API host (local Launchpad).
Do not use this path when the API is remote from the user's browser machine.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import AsyncSessionLocal
from app.core.events import get_redis
from app.core.logging import get_logger
from app.schemas.cloud import CloudCredentials
from app.schemas.cloud_oauth import (
    CloudOAuthCapabilities,
    CloudOAuthProviderName,
    CloudOAuthSessionStatus,
    CloudOAuthStartRequest,
)
from app.services.user_credentials import UserCloudCredentialsService

logger = get_logger(__name__)

_SESSION_TTL_SECONDS = 600
_REDIS_KEY = "launchpad:cloud_oauth:session:{session_id}"


class CloudOAuthError(Exception):
    def __init__(self, message: str, *, code: str = "cloud_oauth_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class CloudOAuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def capabilities(self) -> CloudOAuthCapabilities:
        return CloudOAuthCapabilities(
            gcp=bool((self._settings.gcp_oauth_client_id or "").strip()),
            aws=True,
            azure=bool((self._settings.azure_oauth_client_id or "").strip()),
        )

    async def start(
        self,
        user_id: UUID,
        payload: CloudOAuthStartRequest,
    ) -> CloudOAuthSessionStatus:
        caps = self.capabilities()
        if payload.provider == CloudOAuthProviderName.GCP and not caps.gcp:
            raise CloudOAuthError(
                "GCP OAuth is not configured. Set GCP_OAUTH_CLIENT_ID "
                "(Google Cloud Desktop OAuth client).",
                code="gcp_oauth_not_configured",
            )
        if payload.provider == CloudOAuthProviderName.AZURE and not caps.azure:
            raise CloudOAuthError(
                "Azure OAuth is not configured. Set AZURE_OAUTH_CLIENT_ID "
                "(Entra public/native app client ID).",
                code="azure_oauth_not_configured",
            )
        if payload.provider == CloudOAuthProviderName.AWS:
            if not (payload.aws_start_url or "").strip():
                raise CloudOAuthError(
                    "aws_start_url is required (IAM Identity Center start URL)",
                    code="aws_start_url_required",
                )
            if not (payload.aws_region or "").strip():
                raise CloudOAuthError(
                    "aws_region is required for AWS SSO",
                    code="aws_region_required",
                )

        session_id = str(uuid4())
        record = {
            "session_id": session_id,
            "user_id": str(user_id),
            "provider": payload.provider.value,
            "status": "pending",
            "message": "Complete sign-in in the browser window that opened on this machine",
            "email": None,
            "label": None,
            "created_at": datetime.now(UTC).isoformat(),
            "options": payload.model_dump(mode="json"),
        }
        await self._save_session(session_id, record)
        asyncio.create_task(
            self._run_login(session_id=session_id, user_id=user_id, payload=payload),
            name=f"cloud-oauth-{session_id}",
        )
        return CloudOAuthSessionStatus(
            session_id=session_id,
            provider=payload.provider,
            status="pending",
            message=record["message"],
        )

    async def get_session(self, user_id: UUID, session_id: str) -> CloudOAuthSessionStatus:
        record = await self._load_session(session_id)
        if record is None:
            raise CloudOAuthError("OAuth session not found or expired", code="session_not_found")
        if record.get("user_id") != str(user_id):
            raise CloudOAuthError("OAuth session not found or expired", code="session_not_found")
        return CloudOAuthSessionStatus(
            session_id=session_id,
            provider=CloudOAuthProviderName(record["provider"]),
            status=record["status"],
            message=record.get("message"),
            email=record.get("email"),
            label=record.get("label"),
        )

    async def _run_login(
        self,
        *,
        session_id: str,
        user_id: UUID,
        payload: CloudOAuthStartRequest,
    ) -> None:
        try:
            token_set = await asyncio.to_thread(self._blocking_login, payload)
            label = token_set.email or token_set.subject or payload.provider.value
            async with AsyncSessionLocal() as session:
                vault = UserCloudCredentialsService(session)
                existing = await vault.get_credentials(user_id)
                updates: dict[str, Any] = {}
                token_json = token_set.model_dump_json()
                if payload.provider == CloudOAuthProviderName.GCP:
                    updates["gcp_oauth_token_json"] = token_json
                elif payload.provider == CloudOAuthProviderName.AWS:
                    claims = dict(token_set.claims or {})
                    claims["start_url"] = (payload.aws_start_url or "").strip()
                    claims["region"] = (payload.aws_region or "").strip()
                    token_set = token_set.model_copy(update={"claims": claims})
                    updates["aws_oauth_token_json"] = token_set.model_dump_json()
                    if payload.aws_account_id:
                        updates["aws_sso_account_id"] = payload.aws_account_id.strip()
                    if payload.aws_role_name:
                        updates["aws_sso_role_name"] = payload.aws_role_name.strip()
                else:
                    claims = dict(token_set.claims or {})
                    if payload.azure_tenant_id:
                        claims["tenant_id"] = payload.azure_tenant_id.strip()
                    token_set = token_set.model_copy(update={"claims": claims})
                    updates["azure_oauth_token_json"] = token_set.model_dump_json()
                    if payload.azure_subscription_id:
                        updates["azure_subscription_id"] = payload.azure_subscription_id.strip()
                    client_id = str(claims.get("client_id") or self._settings.azure_oauth_client_id or "")
                    if client_id:
                        updates["azure_client_id"] = client_id
                    tenant = str(claims.get("tenant_id") or self._settings.azure_oauth_tenant_id or "")
                    if tenant:
                        updates["azure_tenant_id"] = tenant

                merged = existing.model_copy(update=updates)
                await vault.replace_credentials(user_id, merged)

            await self._patch_session(
                session_id,
                status="succeeded",
                message=f"Connected as {label}",
                email=token_set.email,
                label=str(label),
            )
            logger.info(
                "cloud_oauth_succeeded",
                provider=payload.provider.value,
                user_id=str(user_id),
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cloud_oauth_failed",
                provider=payload.provider.value,
                user_id=str(user_id),
                session_id=session_id,
                error=str(exc),
            )
            await self._patch_session(
                session_id,
                status="failed",
                message=str(exc)[:500],
            )

    def _blocking_login(self, payload: CloudOAuthStartRequest):
        from pkg.auth.oauth_loopback import (
            AwsSsoOAuthProvider,
            AzureOAuthProvider,
            GcpOAuthProvider,
            login_with_provider,
        )

        timeout = float(self._settings.cloud_oauth_timeout_seconds)
        if payload.provider == CloudOAuthProviderName.GCP:
            client_id = (self._settings.gcp_oauth_client_id or "").strip()
            provider = GcpOAuthProvider(
                client_id=client_id,
                client_secret=self._settings.gcp_oauth_client_secret,
            )
            return login_with_provider(provider, timeout_seconds=timeout)

        if payload.provider == CloudOAuthProviderName.AWS:
            provider = AwsSsoOAuthProvider(
                start_url=(payload.aws_start_url or "").strip(),
                region=(payload.aws_region or "").strip(),
            )
            return login_with_provider(provider, timeout_seconds=timeout)

        tenant = (payload.azure_tenant_id or self._settings.azure_oauth_tenant_id or "common").strip()
        provider = AzureOAuthProvider(
            client_id=(self._settings.azure_oauth_client_id or "").strip(),
            tenant_id=tenant,
        )
        return login_with_provider(provider, timeout_seconds=timeout)

    async def _save_session(self, session_id: str, record: dict[str, Any]) -> None:
        client = await get_redis()
        await client.set(
            _REDIS_KEY.format(session_id=session_id),
            json.dumps(record),
            ex=_SESSION_TTL_SECONDS,
        )

    async def _load_session(self, session_id: str) -> dict[str, Any] | None:
        client = await get_redis()
        raw = await client.get(_REDIS_KEY.format(session_id=session_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def _patch_session(self, session_id: str, **updates: Any) -> None:
        record = await self._load_session(session_id)
        if record is None:
            return
        record.update(updates)
        await self._save_session(session_id, record)

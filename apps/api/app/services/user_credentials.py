"""Account-level cloud credential vault service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import (
    decrypt_secret,
    encrypt_secret,
    gcp_wif_complete,
    has_aws_auth,
    has_gcp_auth,
)
from app.models.domain import UserCloudCredentialStore
from app.schemas.cloud import CloudCredentials
from app.schemas.user_credentials import (
    UserCloudCredentialsStatus,
    UserCloudCredentialsUpdate,
)


class UserCloudCredentialsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_status(self, user_id: UUID) -> UserCloudCredentialsStatus:
        row = await self._get_row(user_id)
        if row is None:
            return UserCloudCredentialsStatus()
        creds = CloudCredentials.model_validate_json(decrypt_secret(row.encrypted_payload))
        return UserCloudCredentialsStatus(
            has_gcp=has_gcp_auth(creds),
            has_aws=has_aws_auth(creds),
            has_azure=bool(
                creds.azure_client_id
                and creds.azure_client_secret
                and creds.azure_tenant_id
                and creds.azure_subscription_id
            ),
            has_cloudflare=bool(creds.cloudflare_api_token),
            gcp_label=self._gcp_label(creds),
            aws_label=self._aws_label(creds),
            azure_label=(
                f"SP {creds.azure_client_id[:8]}…"
                if creds.azure_client_id
                else None
            ),
            cloudflare_label="API token" if creds.cloudflare_api_token else None,
            updated_at=row.updated_at,
        )

    async def get_credentials(self, user_id: UUID) -> CloudCredentials:
        row = await self._get_row(user_id)
        if row is None:
            return CloudCredentials()
        return CloudCredentials.model_validate_json(decrypt_secret(row.encrypted_payload))

    async def upsert(
        self,
        user_id: UUID,
        payload: UserCloudCredentialsUpdate,
    ) -> UserCloudCredentialsStatus:
        existing = await self.get_credentials(user_id)
        merged = existing.model_copy()
        incoming = payload.credentials

        if payload.clear_gcp:
            merged = merged.model_copy(
                update={
                    "gcp_sa_key_json": None,
                    "gcp_wif_project_number": None,
                    "gcp_wif_pool_id": None,
                    "gcp_wif_provider_id": None,
                    "gcp_wif_target_sa_email": None,
                }
            )
        else:
            merged = self._merge_nonempty(
                merged,
                incoming,
                (
                    "gcp_sa_key_json",
                    "gcp_wif_project_number",
                    "gcp_wif_pool_id",
                    "gcp_wif_provider_id",
                    "gcp_wif_target_sa_email",
                ),
            )

        if payload.clear_aws:
            merged = merged.model_copy(
                update={
                    "aws_access_key_id": None,
                    "aws_secret_access_key": None,
                    "aws_session_token": None,
                    "aws_role_arn": None,
                    "aws_role_session_name": None,
                }
            )
        else:
            merged = self._merge_nonempty(
                merged,
                incoming,
                (
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_session_token",
                    "aws_role_arn",
                    "aws_role_session_name",
                ),
            )

        if payload.clear_azure:
            merged = merged.model_copy(
                update={
                    "azure_client_id": None,
                    "azure_client_secret": None,
                    "azure_tenant_id": None,
                    "azure_subscription_id": None,
                }
            )
        else:
            merged = self._merge_nonempty(
                merged,
                incoming,
                (
                    "azure_client_id",
                    "azure_client_secret",
                    "azure_tenant_id",
                    "azure_subscription_id",
                ),
            )

        if payload.clear_cloudflare:
            merged = merged.model_copy(update={"cloudflare_api_token": None})
        else:
            merged = self._merge_nonempty(merged, incoming, ("cloudflare_api_token",))

        ciphertext = encrypt_secret(merged.model_dump_json())
        row = await self._get_row(user_id)
        if row is None:
            row = UserCloudCredentialStore(user_id=user_id, encrypted_payload=ciphertext)
            self._session.add(row)
        else:
            row.encrypted_payload = ciphertext
        await self._session.commit()
        return await self.get_status(user_id)

    async def clear_all(self, user_id: UUID) -> UserCloudCredentialsStatus:
        row = await self._get_row(user_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.commit()
        return UserCloudCredentialsStatus()

    async def _get_row(self, user_id: UUID) -> UserCloudCredentialStore | None:
        result = await self._session.execute(
            select(UserCloudCredentialStore).where(UserCloudCredentialStore.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _merge_nonempty(
        base: CloudCredentials,
        incoming: CloudCredentials,
        fields: tuple[str, ...],
    ) -> CloudCredentials:
        updates: dict[str, str | None] = {}
        data = incoming.model_dump()
        for key in fields:
            value = data.get(key)
            if value is not None and str(value).strip():
                updates[key] = value
        return base.model_copy(update=updates) if updates else base

    @staticmethod
    def _gcp_label(creds: CloudCredentials) -> str | None:
        if gcp_wif_complete(creds):
            return f"WIF pool {creds.gcp_wif_pool_id}"
        if creds.gcp_sa_key_json:
            return "Service account JSON"
        return None

    @staticmethod
    def _aws_label(creds: CloudCredentials) -> str | None:
        if creds.aws_role_arn:
            return f"Role {creds.aws_role_arn.split('/')[-1]}"
        if creds.aws_access_key_id:
            return f"Key {creds.aws_access_key_id[:4]}…"
        return None

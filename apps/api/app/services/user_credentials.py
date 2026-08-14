"""Account-level cloud credential vault service."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import (
    decrypt_secret,
    encrypt_secret,
    gcp_wif_complete,
    has_aws_auth,
    has_azure_auth,
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
            has_azure=has_azure_auth(creds),
            has_cloudflare=bool(creds.cloudflare_api_token),
            has_gcp_sa=bool((creds.gcp_sa_key_json or "").strip()),
            has_gcp_oauth=bool((creds.gcp_oauth_token_json or "").strip()),
            gcp_label=self._gcp_label(creds),
            aws_label=self._aws_label(creds),
            azure_label=self._azure_label(creds),
            cloudflare_label="API token" if creds.cloudflare_api_token else None,
            gcp_project_id=(creds.gcp_project_id or "").strip()
            or self._project_id_from_sa_json(creds.gcp_sa_key_json),
            gcp_region=(creds.gcp_region or "").strip() or None,
            aws_region=(creds.aws_region or "").strip() or None,
            azure_location=(creds.azure_location or "").strip() or None,
            updated_at=row.updated_at,
        )

    async def get_credentials(self, user_id: UUID) -> CloudCredentials:
        row = await self._get_row(user_id)
        if row is None:
            return CloudCredentials()
        return CloudCredentials.model_validate_json(decrypt_secret(row.encrypted_payload))

    async def replace_credentials(
        self,
        user_id: UUID,
        credentials: CloudCredentials,
    ) -> UserCloudCredentialsStatus:
        """Overwrite the vault payload (used after interactive OAuth connect)."""
        ciphertext = encrypt_secret(credentials.model_dump_json())
        row = await self._get_row(user_id)
        if row is None:
            row = UserCloudCredentialStore(user_id=user_id, encrypted_payload=ciphertext)
            self._session.add(row)
        else:
            row.encrypted_payload = ciphertext
        await self._session.commit()
        return await self.get_status(user_id)

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
                    "gcp_project_id": None,
                    "gcp_region": None,
                    "gcp_wif_project_number": None,
                    "gcp_wif_pool_id": None,
                    "gcp_wif_provider_id": None,
                    "gcp_wif_target_sa_email": None,
                    "gcp_oauth_token_json": None,
                }
            )
        else:
            merged = self._merge_nonempty(
                merged,
                incoming,
                (
                    "gcp_sa_key_json",
                    "gcp_project_id",
                    "gcp_region",
                    "gcp_wif_project_number",
                    "gcp_wif_pool_id",
                    "gcp_wif_provider_id",
                    "gcp_wif_target_sa_email",
                    "gcp_oauth_token_json",
                ),
            )
            merged = self._sync_gcp_project_from_sa(merged)

        if payload.clear_aws:
            merged = merged.model_copy(
                update={
                    "aws_access_key_id": None,
                    "aws_secret_access_key": None,
                    "aws_session_token": None,
                    "aws_region": None,
                    "aws_role_arn": None,
                    "aws_role_session_name": None,
                    "aws_oauth_token_json": None,
                    "aws_sso_account_id": None,
                    "aws_sso_role_name": None,
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
                    "aws_region",
                    "aws_role_arn",
                    "aws_role_session_name",
                    "aws_oauth_token_json",
                    "aws_sso_account_id",
                    "aws_sso_role_name",
                ),
            )

        if payload.clear_azure:
            merged = merged.model_copy(
                update={
                    "azure_client_id": None,
                    "azure_client_secret": None,
                    "azure_tenant_id": None,
                    "azure_subscription_id": None,
                    "azure_location": None,
                    "azure_oauth_token_json": None,
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
                    "azure_location",
                    "azure_oauth_token_json",
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
    def _project_id_from_sa_json(sa_json: str | None) -> str | None:
        raw = (sa_json or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        project = parsed.get("project_id")
        if isinstance(project, str) and project.strip():
            return project.strip()
        return None

    @classmethod
    def _sync_gcp_project_from_sa(cls, creds: CloudCredentials) -> CloudCredentials:
        """Fill gcp_project_id from SA JSON when the field is empty."""
        if (creds.gcp_project_id or "").strip():
            return creds
        project = cls._project_id_from_sa_json(creds.gcp_sa_key_json)
        if not project:
            return creds
        return creds.model_copy(update={"gcp_project_id": project})

    @staticmethod
    def _gcp_label(creds: CloudCredentials) -> str | None:
        project = (creds.gcp_project_id or "").strip()
        if not project:
            project = UserCloudCredentialsService._project_id_from_sa_json(creds.gcp_sa_key_json) or ""
        if creds.gcp_sa_key_json:
            return f"Service account JSON ({project})" if project else "Service account JSON"
        if gcp_wif_complete(creds):
            base = f"WIF pool {creds.gcp_wif_pool_id}"
            return f"{base} / {project}" if project else base
        if creds.gcp_oauth_token_json:
            return f"Connected Google ({project})" if project else "Connected Google account"
        if project:
            return f"Project {project}"
        return None

    @staticmethod
    def _aws_label(creds: CloudCredentials) -> str | None:
        if creds.aws_oauth_token_json:
            if creds.aws_sso_account_id and creds.aws_sso_role_name:
                return f"SSO {creds.aws_sso_account_id}/{creds.aws_sso_role_name}"
            return "Connected AWS SSO"
        if creds.aws_role_arn:
            return f"Role {creds.aws_role_arn.split('/')[-1]}"
        if creds.aws_access_key_id:
            return f"Key {creds.aws_access_key_id[:4]}…"
        return None

    @staticmethod
    def _azure_label(creds: CloudCredentials) -> str | None:
        if creds.azure_oauth_token_json:
            return "Connected Microsoft account"
        if creds.azure_client_id:
            return f"SP {creds.azure_client_id[:8]}…"
        return None

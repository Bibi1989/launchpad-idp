"""Per-user encrypted credential vault for plugin cloud providers.

Stores the flexible key/value credentials the provider registry declares (hetzner
api_token, digitalocean api_token, railway token, cloudflare token, ...) as a single
encrypted JSON object keyed by provider id. Secret values are never returned by the
status API - only which fields are set.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.domain import ProviderCredentialStore

logger = get_logger(__name__)


class ProviderCredentialsVault:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_row(self, user_id: UUID) -> ProviderCredentialStore | None:
        result = await self._session.execute(
            select(ProviderCredentialStore).where(ProviderCredentialStore.user_id == user_id)
        )
        return result.scalar_one_or_none()

    def _decode(self, row: ProviderCredentialStore | None) -> dict[str, dict[str, str]]:
        if row is None:
            return {}
        try:
            data = json.loads(decrypt_secret(row.encrypted_payload))
            if isinstance(data, dict):
                return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        except Exception as exc:  # noqa: BLE001 - unreadable vault -> treat as empty
            logger.warning("provider_credentials_vault_unreadable", error=str(exc)[:200])
        return {}

    async def _store(self, user_id: UUID, all_creds: dict[str, dict[str, str]]) -> None:
        ciphertext = encrypt_secret(json.dumps(all_creds))
        row = await self._get_row(user_id)
        if row is None:
            self._session.add(ProviderCredentialStore(user_id=user_id, encrypted_payload=ciphertext))
        else:
            row.encrypted_payload = ciphertext
        await self._session.commit()

    async def get_for_provider(self, user_id: UUID, provider_id: str) -> dict[str, str]:
        """Decrypted credentials for one provider (empty if none). Server-side use only."""
        return self._decode(await self._get_row(user_id)).get(provider_id, {})

    async def status(self, user_id: UUID) -> dict[str, list[str]]:
        """Which fields are configured per provider - never the secret values."""
        decoded = self._decode(await self._get_row(user_id))
        return {
            provider_id: [k for k, v in fields.items() if str(v).strip()]
            for provider_id, fields in decoded.items()
            if any(str(v).strip() for v in fields.values())
        }

    async def upsert_provider(
        self,
        user_id: UUID,
        provider_id: str,
        credentials: dict[str, str],
    ) -> dict[str, list[str]]:
        """Merge non-empty fields for a provider; empty string clears a field."""
        row = await self._get_row(user_id)
        decoded = self._decode(row)
        current = dict(decoded.get(provider_id, {}))
        for key, value in credentials.items():
            clean = str(value or "").strip()
            if clean:
                current[key] = clean
            else:
                current.pop(key, None)
        if current:
            decoded[provider_id] = current
        else:
            decoded.pop(provider_id, None)
        await self._store(user_id, decoded)
        return await self.status(user_id)

    async def delete_provider(self, user_id: UUID, provider_id: str) -> dict[str, list[str]]:
        row = await self._get_row(user_id)
        decoded = self._decode(row)
        if provider_id in decoded:
            decoded.pop(provider_id, None)
            await self._store(user_id, decoded)
        return await self.status(user_id)

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SENSITIVE_ENV_KEYS = {
    "GCP_SA_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "CLOUDFLARE_API_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "KUBECONFIG",
}

_VALUE_PATTERN = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization|credential|"
    r"aws_secret_access_key|aws_access_key_id|gcp_sa_key|private[_-]?key)"
    r"\s*[:=]\s*\S+"
)

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*")
_GITHUB_TOKEN_PATTERN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


def _derive_fernet_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key_material = settings.secrets_encryption_key
    if not key_material:
        # Ephemeral key for local/dev — never use empty key in production.
        key_material = os.environ.get("LAUNCHPAD_DEV_SECRET_KEY") or secrets.token_urlsafe(32)
        logger.warning("secrets_encryption_key_missing_using_ephemeral_dev_key")
    return Fernet(_derive_fernet_key(key_material))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret — invalid key or ciphertext") from exc


def mask_secret_value(value: str, *, visible: int = 4) -> str:
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * max(8, len(value) - visible)}{value[-visible:]}"


def mask_terminal_output(chunk: str) -> str:
    masked = _VALUE_PATTERN.sub(r"\1=[REDACTED]", chunk)
    masked = _BEARER_PATTERN.sub(r"\1[REDACTED]", masked)
    masked = _GITHUB_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", masked)
    for key in _SENSITIVE_ENV_KEYS:
        masked = re.sub(
            rf"(?i)({re.escape(key)}\s*[:=]\s*)\S+",
            r"\1[REDACTED]",
            masked,
        )
    return masked


def credentials_to_env(credentials_map: dict[str, str | None]) -> dict[str, str]:
    """Build sandbox env vars, omitting empty values. Never log return values."""
    env: dict[str, str] = {}
    for key, value in credentials_map.items():
        if value:
            env[key] = value
    return env

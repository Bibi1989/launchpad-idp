from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.core.config import Settings, get_settings

DEV_USER_EMAIL = "dev@launchpad.local"
DEV_USER_DISPLAY_NAME = "Dev User"
DEV_USER_PASSWORD = "dev-password-change-me"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    user_id: UUID,
    email: str,
    org_id: UUID | None = None,
    org_role: str | None = None,
    settings: Settings | None = None,
) -> str:
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iss": "launchpad-idp",
        "iat": now,
        "exp": now + timedelta(minutes=cfg.jwt_expire_minutes),
    }
    if org_id is not None:
        payload["org_id"] = str(org_id)
    if org_role is not None:
        payload["org_role"] = org_role
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=[cfg.jwt_algorithm],
            issuer="launchpad-idp",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Invalid or expired access token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = payload.get("sub")
    if not subject or not isinstance(subject, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "Token missing subject"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload

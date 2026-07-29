from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import decode_access_token
from app.models.domain import User
from app.repositories.user import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    token: Annotated[str | None, Query()] = None,
) -> User:
    raw_token = credentials.credentials if credentials is not None else token
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(raw_token)
    user_id = UUID(str(payload["sub"]))
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "User not found"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.user_id = str(user.id)
    return user


async def get_user_from_websocket(websocket: WebSocket) -> User | None:
    """Authenticate a WebSocket via ?token= query param. Returns None if invalid."""
    raw_token = websocket.query_params.get("token")
    if not raw_token:
        return None
    try:
        payload = decode_access_token(raw_token)
        user_id = UUID(str(payload["sub"]))
    except (HTTPException, ValueError, TypeError):
        return None

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return await UserRepository(session).get_by_id(user_id)


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]

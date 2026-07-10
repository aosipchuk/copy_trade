import asyncio
import json
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis_client import get_redis_client
from app.core.security import JTI_REVOKED_PREFIX, decode_access_token
from app.models.user import User

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)
TRADER_EXPORT_TICKET_PREFIX = "export:trader:"
TRADER_EXPORT_TICKET_TTL_SECONDS = 5 * 60


async def _user_from_id(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


async def _user_from_access_token(token: str, db: AsyncSession) -> User:
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    jti: str | None = payload.get("jti")
    if jti:
        r = get_redis_client()
        is_revoked: int = await asyncio.to_thread(
            r.exists, f"{JTI_REVOKED_PREFIX}{jti}"
        )
        if is_revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
            )

    return await _user_from_id(payload["user_id"], db)


async def get_current_user_from_bearer_or_export_ticket(
    trader_id: int,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_bearer_scheme)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    ticket: Annotated[str | None, Query()] = None,
) -> User:
    if credentials is not None:
        return await _user_from_access_token(credentials.credentials, db)

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    r = get_redis_client()
    raw: str | None = await asyncio.to_thread(
        r.get, f"{TRADER_EXPORT_TICKET_PREFIX}{ticket}"
    )
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired export ticket",
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid export ticket",
        ) from exc

    if payload.get("trader_id") != trader_id or not isinstance(
        payload.get("user_id"), int
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid export ticket",
        )

    return await _user_from_id(payload["user_id"], db)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    return await _user_from_access_token(credentials.credentials, db)


async def require_admin_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.telegram_id not in settings.admin_telegram_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin_user)]
DBSession = Annotated[AsyncSession, Depends(get_db)]

import asyncio
import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, bearer_scheme
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.redis_client import get_redis_client
from app.core.security import (
    JTI_REVOKED_PREFIX,
    create_access_token,
    decode_access_token,
    validate_telegram_init_data,
)
from app.models.user import User
from app.schemas.auth import TelegramAuthRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=TokenResponse)
@limiter.limit("10/minute")
async def telegram_auth(
    request: Request, body: TelegramAuthRequest, db: DBSession
) -> TokenResponse:
    data = validate_telegram_init_data(body.init_data)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData",
        )

    user_json = data.get("user", "{}")
    user_info: dict[str, Any] = json.loads(user_json)
    telegram_id = int(user_info["id"])

    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=user_info.get("username"),
            first_name=user_info.get("first_name"),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

    token = create_access_token(telegram_id=telegram_id, user_id=user.id)
    return TokenResponse(access_token=token, user_id=user.id, telegram_id=telegram_id)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        telegram_id=current_user.telegram_id,
        username=current_user.username,
        first_name=current_user.first_name,
        hl_address=current_user.hl_address,
        is_admin=current_user.telegram_id in settings.admin_telegram_ids,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> None:
    """Revoke the current JWT by adding its jti to the Redis blocklist."""
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        return  # already expired — nothing to revoke

    jti: str | None = payload.get("jti")
    if not jti:
        return  # legacy token without jti — will expire naturally

    remaining = int(payload.get("exp", 0) - time.time())
    if remaining > 0:
        r = get_redis_client()
        await asyncio.to_thread(r.setex, f"{JTI_REVOKED_PREFIX}{jti}", remaining, "1")

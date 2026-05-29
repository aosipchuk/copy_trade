import json

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.core.security import create_access_token, validate_telegram_init_data
from app.models.user import User
from app.schemas.auth import TelegramAuthRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=TokenResponse)
async def telegram_auth(body: TelegramAuthRequest, db: DBSession) -> TokenResponse:
    data = validate_telegram_init_data(body.init_data)
    if data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram initData")

    user_json = data.get("user", "{}")
    user_info: dict = json.loads(user_json)
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
    )

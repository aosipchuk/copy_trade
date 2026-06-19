import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, unquote

from jose import JWTError, jwt

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_SECONDS = 86400 * 7  # 7 days
JTI_REVOKED_PREFIX = "auth:revoked:"


def validate_telegram_init_data(init_data: str) -> dict[str, str] | None:
    """
    Validates Telegram WebApp initData using HMAC-SHA256.
    Returns parsed data dict if valid, None if invalid.
    """
    params = dict(parse_qsl(unquote(init_data), keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))

    secret_key = hmac.new(
        b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        logger.warning("telegram_init_data_invalid_hash")
        return None

    auth_date = int(params.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        logger.warning(
            "telegram_init_data_expired", age_seconds=time.time() - auth_date
        )
        return None

    return params


def create_access_token(telegram_id: int, user_id: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(telegram_id),
        "user_id": user_id,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ACCESS_TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)  # type: ignore[no-any-return]


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])  # type: ignore[no-any-return]
    except JWTError:
        return None

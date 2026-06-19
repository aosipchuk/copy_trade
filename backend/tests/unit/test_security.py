import hashlib
import hmac
import json
import time
import urllib.parse

from app.core.security import (
    create_access_token,
    decode_access_token,
    validate_telegram_init_data,
)


def make_init_data(telegram_id: int = 123456789, bot_token: str = "test_token") -> str:
    user = json.dumps({"id": telegram_id, "first_name": "Test", "username": "testuser"})
    auth_date = str(int(time.time()))
    params = {"user": user, "auth_date": auth_date}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    hash_val = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    params["hash"] = hash_val
    return urllib.parse.urlencode(params)


class TestTelegramInitDataValidation:
    def test_valid_init_data(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.core.security.settings.telegram_bot_token", "test_token"
        )
        init_data = make_init_data(bot_token="test_token")
        result = validate_telegram_init_data(init_data)
        assert result is not None
        assert "user" in result
        assert "auth_date" in result

    def test_invalid_hash(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.core.security.settings.telegram_bot_token", "test_token"
        )
        init_data = make_init_data(bot_token="wrong_token")
        result = validate_telegram_init_data(init_data)
        assert result is None

    def test_missing_hash(self) -> None:
        result = validate_telegram_init_data("user=test&auth_date=123")
        assert result is None


class TestJWT:
    def test_create_and_decode_token(self) -> None:
        token = create_access_token(telegram_id=123, user_id=1)
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "123"
        assert payload["user_id"] == 1

    def test_invalid_token_returns_none(self) -> None:
        result = decode_access_token("invalid.token.here")
        assert result is None

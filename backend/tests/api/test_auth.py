"""Integration tests for POST /auth/telegram."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_init_data(
    bot_token: str, user_id: int = 12345, username: str = "testuser"
) -> str:
    """Build a valid Telegram initData string signed with bot_token."""
    user_data = json.dumps({"id": user_id, "username": username, "first_name": "Test"})
    fields = {
        "user": user_data,
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohBzDFSy",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


class TestAuthEndpoint:
    @pytest.mark.asyncio
    async def test_valid_telegram_init_data_returns_token(self, client) -> None:
        bot_token = "123456:test"  # matches TELEGRAM_BOT_TOKEN in test env
        init_data = _make_init_data(bot_token)
        response = await client.post(
            "/api/auth/telegram", json={"init_data": init_data}
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert len(body["access_token"]) > 20

    @pytest.mark.asyncio
    async def test_invalid_hash_returns_401(self, client) -> None:
        response = await client.post(
            "/api/auth/telegram",
            json={"init_data": "user=bad&hash=00000000&auth_date=1"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_init_data_returns_422(self, client) -> None:
        response = await client.post("/api/auth/telegram", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_second_login_same_user_returns_same_structure(self, client) -> None:
        bot_token = "123456:test"
        init_data = _make_init_data(bot_token)
        r1 = await client.post("/api/auth/telegram", json={"init_data": init_data})
        r2 = await client.post("/api/auth/telegram", json={"init_data": init_data})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["token_type"] == r2.json()["token_type"]

"""Integration tests for builder fee wallet endpoints."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_init_data(
    bot_token: str, user_id: int = 99001, username: str = "buildertest"
) -> str:
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "Builder"}
    )
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


async def _get_jwt(client) -> str:
    resp = await client.post(
        "/api/auth/telegram",
        json={"init_data": _make_init_data("123456:test")},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


class TestBuilderFeeApproval:
    @pytest.mark.asyncio
    async def test_builder_setup_returns_503_when_not_configured(self, client) -> None:
        """GET /wallet/builder-setup returns 503 when BUILDER_ADDRESS is empty."""
        token = await _get_jwt(client)
        resp = await client.get(
            "/api/wallet/builder-setup",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Default config has builder_address="" so expect 503
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_builder_approve_records_timestamp(self, client) -> None:
        """POST /wallet/builder-approve sets builder_fee_approved_at on User."""
        from unittest.mock import patch

        token = await _get_jwt(client)

        # Patch hl_skip_approve=True and builder_address so endpoint doesn't call HL
        with patch("app.api.wallet.settings") as mock_settings:
            mock_settings.builder_address = "0xbuilder000000000000000000000000000000"
            mock_settings.builder_fee_rate = 50
            mock_settings.builder_max_fee_rate = "0.075%"
            mock_settings.hl_skip_approve = True

            resp = await client.post(
                "/api/wallet/builder-approve",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "nonce": 1_700_000_000_000,
                    "signature": {
                        "r": "0x" + "aa" * 32,
                        "s": "0x" + "bb" * 32,
                        "v": 27,
                    },
                },
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_agent_status_includes_builder_fee_flag(self, client) -> None:
        """GET /wallet/status always includes builder_fee_approved field."""
        token = await _get_jwt(client)
        resp = await client.get(
            "/api/wallet/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "builder_fee_approved" in body
        assert isinstance(body["builder_fee_approved"], bool)

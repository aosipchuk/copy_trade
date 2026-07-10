"""Integration tests for /subscriptions CRUD endpoints."""

import hashlib
import hmac
import itertools
import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from sqlalchemy import insert, update

from app.models.subscription import Subscription
from app.models.trader import Trader, TraderStat
from app.models.user import User
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary

pytestmark = pytest.mark.asyncio(loop_scope="session")

_RICH_MARGIN = MarginSummary(
    accountValue=Decimal("50000"),
    totalMarginUsed=Decimal("0"),
    totalRawUsd=Decimal("50000"),
)


@pytest.fixture(autouse=True)
def _mock_hl_margin():
    """Patch HL account-summary for all subscription tests (no live API calls)."""
    with patch.object(
        HyperliquidInfoClient,
        "get_account_summary",
        AsyncMock(return_value=_RICH_MARGIN),
    ):
        yield


# Each call to _seed_trader uses a fresh address so the unique constraint never fires.
_addr_counter: itertools.count = itertools.count(1)

_FAKE_HL_ADDRESS = "0x" + "ab" * 20


def _make_init_data(user_id: int, username: str = "subtest") -> str:
    bot_token = "123456:test"
    user_data = json.dumps({"id": user_id, "username": username, "first_name": "Sub"})
    fields = {"user": user_data, "auth_date": str(int(time.time())), "query_id": "test"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


async def _seed_trader(db_session) -> int:
    n = next(_addr_counter)
    address = f"0xcccc{n:036x}"
    result = await db_session.execute(
        insert(Trader)
        .values(hl_address=address, is_active=True, has_perp_activity=True)
        .returning(Trader.id)
    )
    trader_id = result.scalar_one()
    await db_session.execute(
        insert(TraderStat).values(
            trader_id=trader_id,
            period="week",
            pnl_usd=5000,
            roi_pct=10,
            volume_usd=100000,
        )
    )
    await db_session.commit()
    return trader_id


async def _auth(client, db_session, user_id: int) -> dict[str, str]:
    """Authenticate user and set hl_address so they can create subscriptions."""
    r = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert r.status_code == 200, f"Auth failed: {r.text}"
    db_user_id = r.json()["user_id"]
    # Set hl_address so the subscription endpoint doesn't reject the request.
    await db_session.execute(
        update(User).where(User.id == db_user_id).values(hl_address=_FAKE_HL_ADDRESS)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _get_auth_header(client, user_id: int = 77777) -> dict[str, str]:
    """Authenticate-only helper (no hl_address needed)."""
    r = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert r.status_code == 200, f"Auth failed: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestSubscriptionsCRUD:
    @pytest.mark.asyncio
    async def test_list_returns_empty_for_new_user(self, client) -> None:
        headers = await _get_auth_header(client, user_id=77001)
        response = await client.get("/api/subscriptions", headers=headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_requires_auth(self, client) -> None:
        response = await client.get("/api/subscriptions")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_subscription_success(self, client, db_session) -> None:
        trader_id = await _seed_trader(db_session)
        headers = await _auth(client, db_session, user_id=77002)
        body = {
            "trader_id": trader_id,
            "max_allocation_usd": 100.0,
            "copy_ratio_pct": 100.0,
            "stop_loss_pct": 20.0,
            "max_leverage": 10.0,
        }
        response = await client.post("/api/subscriptions", json=body, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data["trader_id"] == trader_id
        assert data["is_active"] is True

        subscription = await db_session.get(Subscription, data["id"])
        assert subscription is not None
        assert subscription.source_type == "manual"
        assert subscription.source_id is None
        assert subscription.source_version_id is None
        assert subscription.managed_by_portfolio is False

    @pytest.mark.asyncio
    async def test_create_subscription_invalid_trader_returns_400(
        self, client, db_session
    ) -> None:
        headers = await _auth(client, db_session, user_id=77003)
        body = {
            "trader_id": 999999,
            "max_allocation_usd": 100.0,
            "copy_ratio_pct": 100.0,
            "stop_loss_pct": 20.0,
            "max_leverage": 10.0,
        }
        response = await client.post("/api/subscriptions", json=body, headers=headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_after_create(self, client, db_session) -> None:
        trader_id = await _seed_trader(db_session)
        headers = await _auth(client, db_session, user_id=77004)
        await client.post(
            "/api/subscriptions",
            json={
                "trader_id": trader_id,
                "max_allocation_usd": 50.0,
                "copy_ratio_pct": 50.0,
                "stop_loss_pct": 15.0,
                "max_leverage": 5.0,
            },
            headers=headers,
        )
        response = await client.get("/api/subscriptions", headers=headers)
        assert response.status_code == 200
        subs = response.json()
        assert any(s["trader_id"] == trader_id for s in subs)

    @pytest.mark.asyncio
    async def test_patch_subscription(self, client, db_session) -> None:
        trader_id = await _seed_trader(db_session)
        headers = await _auth(client, db_session, user_id=77005)
        create_r = await client.post(
            "/api/subscriptions",
            json={
                "trader_id": trader_id,
                "max_allocation_usd": 100.0,
                "copy_ratio_pct": 100.0,
                "stop_loss_pct": 20.0,
                "max_leverage": 10.0,
            },
            headers=headers,
        )
        assert create_r.status_code == 201, f"Create failed: {create_r.text}"
        sub_id = create_r.json()["id"]
        patch_r = await client.patch(
            f"/api/subscriptions/{sub_id}",
            json={"stop_loss_pct": 30.0},
            headers=headers,
        )
        assert patch_r.status_code == 200
        assert patch_r.json()["stop_loss_pct"] == 30.0

    @pytest.mark.asyncio
    async def test_delete_subscription(self, client, db_session) -> None:
        trader_id = await _seed_trader(db_session)
        headers = await _auth(client, db_session, user_id=77006)
        create_r = await client.post(
            "/api/subscriptions",
            json={
                "trader_id": trader_id,
                "max_allocation_usd": 100.0,
                "copy_ratio_pct": 100.0,
                "stop_loss_pct": 20.0,
                "max_leverage": 10.0,
            },
            headers=headers,
        )
        assert create_r.status_code == 201, f"Create failed: {create_r.text}"
        sub_id = create_r.json()["id"]
        del_r = await client.delete(f"/api/subscriptions/{sub_id}", headers=headers)
        assert del_r.status_code == 204

    @pytest.mark.asyncio
    async def test_patch_other_users_subscription_returns_404(
        self, client, db_session
    ) -> None:
        trader_id = await _seed_trader(db_session)
        headers = await _auth(client, db_session, user_id=77007)
        create_r = await client.post(
            "/api/subscriptions",
            json={
                "trader_id": trader_id,
                "max_allocation_usd": 100.0,
                "copy_ratio_pct": 100.0,
                "stop_loss_pct": 20.0,
                "max_leverage": 10.0,
            },
            headers=headers,
        )
        assert create_r.status_code == 201, f"Create failed: {create_r.text}"
        sub_id = create_r.json()["id"]
        other_headers = await _get_auth_header(client, user_id=88888)
        r = await client.patch(
            f"/api/subscriptions/{sub_id}",
            json={"stop_loss_pct": 50.0},
            headers=other_headers,
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_validation_rejects_negative_allocation(self, client) -> None:
        headers = await _get_auth_header(client, user_id=77008)
        body = {
            "trader_id": 1,
            "max_allocation_usd": -50.0,
            "copy_ratio_pct": 100.0,
            "stop_loss_pct": 20.0,
            "max_leverage": 10.0,
        }
        response = await client.post("/api/subscriptions", json=body, headers=headers)
        assert response.status_code == 422

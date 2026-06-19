"""Integration tests for demo subscription API endpoints."""

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

from app.models.signal import Signal
from app.models.trade import UserTrade
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

_addr_counter: itertools.count = itertools.count(9000)

_FAKE_HL_ADDRESS = "0x" + "cd" * 20


@pytest.fixture(autouse=True)
def _mock_hl_margin():
    """Patch HL account-summary so live subscription creation works without a real HL account."""
    with patch.object(
        HyperliquidInfoClient,
        "get_account_summary",
        AsyncMock(return_value=_RICH_MARGIN),
    ):
        yield


def _make_init_data(user_id: int, username: str = "demotest") -> str:
    bot_token = "123456:test"
    user_data = json.dumps({"id": user_id, "username": username, "first_name": "Demo"})
    fields = {"user": user_data, "auth_date": str(int(time.time())), "query_id": "test"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


async def _get_auth_header(client, user_id: int) -> dict[str, str]:
    r = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert r.status_code == 200, f"Auth failed: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _auth_with_wallet(client, db_session, user_id: int) -> dict[str, str]:
    r = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert r.status_code == 200, f"Auth failed: {r.text}"
    db_user_id = r.json()["user_id"]
    await db_session.execute(
        update(User).where(User.id == db_user_id).values(hl_address=_FAKE_HL_ADDRESS)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _seed_trader(db_session) -> int:
    n = next(_addr_counter)
    address = f"0xdemo{n:036x}"
    result = await db_session.execute(
        insert(Trader).values(hl_address=address, is_active=True).returning(Trader.id)
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


def _demo_body(trader_id: int, **kwargs) -> dict:
    return {
        "trader_id": trader_id,
        "max_allocation_usd": 100.0,
        "copy_ratio_pct": 100.0,
        "stop_loss_pct": 20.0,
        "max_leverage": 10.0,
        "is_demo": True,
        **kwargs,
    }


class TestCreateDemoSubscription:
    @pytest.mark.asyncio
    async def test_create_without_wallet(self, client, db_session) -> None:
        """Demo subscription can be created without an HL wallet connected."""
        trader_id = await _seed_trader(db_session)
        # User has no hl_address — only authenticated
        headers = await _get_auth_header(client, user_id=80001)

        response = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["is_demo"] is True
        assert data["is_active"] is True
        assert data["trader_id"] == trader_id

    @pytest.mark.asyncio
    async def test_create_live_without_wallet_fails(self, client, db_session) -> None:
        """Live subscription is rejected with 400 when the user has no HL wallet."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80002)
        body = {
            "trader_id": trader_id,
            "max_allocation_usd": 100.0,
            "copy_ratio_pct": 100.0,
            "stop_loss_pct": 20.0,
            "max_leverage": 10.0,
            "is_demo": False,
        }

        response = await client.post("/api/subscriptions", json=body, headers=headers)

        assert response.status_code == 400
        assert "wallet" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_demo_is_active_by_default(self, client, db_session) -> None:
        """Newly created demo subscription has is_active=True."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80003)

        response = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )

        assert response.status_code == 201
        assert response.json()["is_active"] is True

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, client, db_session) -> None:
        """Unauthenticated demo subscription creation is rejected with 401."""
        trader_id = await _seed_trader(db_session)

        response = await client.post("/api/subscriptions", json=_demo_body(trader_id))

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_demo_invalid_trader_returns_400(self, client) -> None:
        """Demo subscription with a non-existent trader_id is rejected with 400."""
        headers = await _get_auth_header(client, user_id=80004)
        body = {**_demo_body(trader_id=999999)}

        response = await client.post("/api/subscriptions", json=body, headers=headers)

        assert response.status_code == 400


class TestListDemoSubscriptions:
    @pytest.mark.asyncio
    async def test_list_is_demo_filter_returns_only_demo(
        self, client, db_session
    ) -> None:
        """GET /subscriptions?is_demo=true returns only demo subscriptions."""
        trader_id = await _seed_trader(db_session)
        headers = await _auth_with_wallet(client, db_session, user_id=80010)

        # Create a live subscription
        live_r = await client.post(
            "/api/subscriptions",
            json={**_demo_body(trader_id), "is_demo": False},
            headers=headers,
        )
        assert live_r.status_code == 201

        # Create a demo subscription
        demo_r = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )
        assert demo_r.status_code == 201
        demo_sub_id = demo_r.json()["id"]

        list_r = await client.get("/api/subscriptions?is_demo=true", headers=headers)

        assert list_r.status_code == 200
        subs = list_r.json()
        assert all(s["is_demo"] is True for s in subs)
        assert any(s["id"] == demo_sub_id for s in subs)

    @pytest.mark.asyncio
    async def test_list_live_filter_returns_only_live(
        self, client, db_session
    ) -> None:
        """GET /subscriptions?is_demo=false returns only live subscriptions."""
        trader_id = await _seed_trader(db_session)
        headers = await _auth_with_wallet(client, db_session, user_id=80011)

        live_r = await client.post(
            "/api/subscriptions",
            json={**_demo_body(trader_id), "is_demo": False},
            headers=headers,
        )
        assert live_r.status_code == 201
        live_sub_id = live_r.json()["id"]

        await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )

        list_r = await client.get("/api/subscriptions?is_demo=false", headers=headers)

        assert list_r.status_code == 200
        subs = list_r.json()
        assert all(s["is_demo"] is False for s in subs)
        assert any(s["id"] == live_sub_id for s in subs)

    @pytest.mark.asyncio
    async def test_demo_not_in_default_listing(self, client, db_session) -> None:
        """GET /subscriptions (no param) excludes demo subscriptions."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80012)

        demo_r = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )
        assert demo_r.status_code == 201
        demo_sub_id = demo_r.json()["id"]

        list_r = await client.get("/api/subscriptions", headers=headers)

        assert list_r.status_code == 200
        sub_ids = [s["id"] for s in list_r.json()]
        assert demo_sub_id not in sub_ids

    @pytest.mark.asyncio
    async def test_list_returns_pnl_fields(self, client, db_session) -> None:
        """Demo subscription list response includes realized_pnl and unrealized_pnl."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80013)

        await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )

        list_r = await client.get("/api/subscriptions?is_demo=true", headers=headers)

        assert list_r.status_code == 200
        sub = list_r.json()[0]
        assert "realized_pnl" in sub
        assert "unrealized_pnl" in sub
        assert "trade_count" in sub


class TestDemoPortfolio:
    @pytest.mark.asyncio
    async def test_portfolio_requires_auth(self, client) -> None:
        """GET /demo/portfolio without a token returns 401."""
        r = await client.get("/api/demo/portfolio")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_portfolio_empty_for_new_user(self, client) -> None:
        """New user with no demo subscriptions gets all-zero aggregates."""
        headers = await _get_auth_header(client, user_id=80020)

        r = await client.get("/api/demo/portfolio", headers=headers)

        assert r.status_code == 200
        data = r.json()
        assert data["total_realized_pnl"] == 0.0
        assert data["total_unrealized_pnl"] == 0.0
        assert data["trade_count"] == 0
        assert data["win_count"] == 0
        assert data["win_rate_pct"] == 0.0
        assert data["open_positions"] == []

    @pytest.mark.asyncio
    async def test_portfolio_aggregate(self, client, db_session) -> None:
        """Portfolio sums realized_pnl of closed demo trades across subscriptions."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80021)

        # Create a demo subscription
        sub_r = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )
        assert sub_r.status_code == 201
        sub_id = sub_r.json()["id"]

        # Seed a signal (FK requirement for UserTrade)
        sig_result = await db_session.execute(
            insert(Signal)
            .values(
                trader_id=trader_id,
                signal_type="CLOSE",
                coin="BTC",
                side="long",
                size=0.01,
            )
            .returning(Signal.id)
        )
        signal_id = sig_result.scalar_one()

        # Seed two closed demo trades: +50 and +30 = +80 realized PnL, both winners
        for pnl in [50.0, 30.0]:
            await db_session.execute(
                insert(UserTrade).values(
                    subscription_id=sub_id,
                    signal_id=signal_id,
                    coin="BTC",
                    side="long",
                    size=0.01,
                    price=55000.0,
                    status="filled",
                    trade_type="close",
                    realized_pnl=pnl,
                    is_demo=True,
                )
            )
        await db_session.commit()

        r = await client.get("/api/demo/portfolio", headers=headers)

        assert r.status_code == 200
        data = r.json()
        assert data["total_realized_pnl"] == pytest.approx(80.0)
        assert data["trade_count"] == 2
        assert data["win_count"] == 2
        assert data["win_rate_pct"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_portfolio_win_rate_with_mixed_results(self, client, db_session) -> None:
        """Win rate = win_count / trade_count * 100 with one win and one loss."""
        trader_id = await _seed_trader(db_session)
        headers = await _get_auth_header(client, user_id=80022)

        sub_r = await client.post(
            "/api/subscriptions", json=_demo_body(trader_id), headers=headers
        )
        assert sub_r.status_code == 201
        sub_id = sub_r.json()["id"]

        sig_result = await db_session.execute(
            insert(Signal)
            .values(
                trader_id=trader_id,
                signal_type="CLOSE",
                coin="ETH",
                side="long",
                size=0.1,
            )
            .returning(Signal.id)
        )
        signal_id = sig_result.scalar_one()

        # One winning (+100) and one losing (-40) trade
        for pnl in [100.0, -40.0]:
            await db_session.execute(
                insert(UserTrade).values(
                    subscription_id=sub_id,
                    signal_id=signal_id,
                    coin="ETH",
                    side="long",
                    size=0.1,
                    price=3000.0,
                    status="filled",
                    trade_type="close",
                    realized_pnl=pnl,
                    is_demo=True,
                )
            )
        await db_session.commit()

        r = await client.get("/api/demo/portfolio", headers=headers)

        data = r.json()
        assert data["trade_count"] == 2
        assert data["win_count"] == 1
        assert data["win_rate_pct"] == pytest.approx(50.0)
        assert data["total_realized_pnl"] == pytest.approx(60.0)

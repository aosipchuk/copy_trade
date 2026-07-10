"""Integration tests for /traders endpoints."""

import hashlib
import hmac
import itertools
import json
import time
from urllib.parse import urlencode

import pytest
from sqlalchemy import insert

from app.models.trader import Trader, TraderStat

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Each call to _seed_traders uses fresh addresses so unique constraint never fires.
_trader_counter: itertools.count = itertools.count(1)
_auth_counter: itertools.count = itertools.count(1)


def _make_init_data(user_id: int, username: str = "tradertest") -> str:
    bot_token = "123456:test"
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "Trader"}
    )
    fields = {"user": user_data, "auth_date": str(int(time.time())), "query_id": "test"}
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


async def _auth_header(client) -> dict[str, str]:
    user_id = 710_000 + next(_auth_counter)
    response = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_traders(db_session) -> list[int]:
    """Insert two test traders with week stats and return their IDs."""
    ids = []
    for suffix in ("aa", "bb"):
        n = next(_trader_counter)
        address = f"0x{suffix}{n:038x}"
        result = await db_session.execute(
            insert(Trader)
            .values(
                hl_address=address,
                display_name=f"Trader-{n}",
                is_active=True,
                has_perp_activity=True,
            )
            .returning(Trader.id)
        )
        trader_id = result.scalar_one()
        ids.append(trader_id)
        await db_session.execute(
            insert(TraderStat).values(
                trader_id=trader_id,
                period="week",
                pnl_usd=float(n) * 1000,
                roi_pct=float(n) * 5,
                volume_usd=float(n) * 50000,
            )
        )
    await db_session.commit()
    return ids


async def _seed_null_roi_trader(db_session) -> str:
    n = next(_trader_counter)
    address = f"0x99{n:038x}"
    result = await db_session.execute(
        insert(Trader)
        .values(
            hl_address=address,
            display_name=f"Partial-Trader-{n}",
            is_active=True,
            has_perp_activity=True,
        )
        .returning(Trader.id)
    )
    trader_id = result.scalar_one()
    await db_session.execute(
        insert(TraderStat).values(
            trader_id=trader_id,
            period="week",
            pnl_usd=2500,
            roi_pct=None,
            volume_usd=75000,
        )
    )
    await db_session.commit()
    return address


class TestTradersList:
    @pytest.mark.asyncio
    async def test_returns_200_with_items(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get("/api/traders", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) >= 2

    @pytest.mark.asyncio
    async def test_response_schema(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get("/api/traders", headers=headers)
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert "id" in item
        assert "hl_address" in item
        assert "stats" in item
        assert isinstance(item["stats"], list)

    @pytest.mark.asyncio
    async def test_period_filter(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get("/api/traders?period=week", headers=headers)
        assert response.status_code == 200
        assert len(response.json()["items"]) >= 1

    @pytest.mark.asyncio
    async def test_sort_by_roi(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get(
            "/api/traders?sort=roi&period=week", headers=headers
        )
        assert response.status_code == 200
        items = response.json()["items"]
        rois = [item["stats"][0]["roi_pct"] for item in items if item["stats"]]
        assert rois == sorted(rois, reverse=True)

    @pytest.mark.asyncio
    async def test_null_sort_metric_hidden_unless_address_search(
        self, client, db_session
    ) -> None:
        address = await _seed_null_roi_trader(db_session)
        headers = await _auth_header(client)

        ranked_response = await client.get(
            "/api/traders?sort=roi&period=week&limit=200",
            headers=headers,
        )
        assert ranked_response.status_code == 200
        ranked_addresses = {
            item["hl_address"] for item in ranked_response.json()["items"]
        }
        assert address not in ranked_addresses

        search_response = await client.get(
            f"/api/traders?sort=roi&period=week&address={address}",
            headers=headers,
        )
        assert search_response.status_code == 200
        search_items = search_response.json()["items"]
        assert [item["hl_address"] for item in search_items] == [address]
        assert search_items[0]["stats"][0]["roi_pct"] is None

    @pytest.mark.asyncio
    async def test_limit_respected(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get("/api/traders?limit=1&period=week", headers=headers)
        assert response.status_code == 200
        assert len(response.json()["items"]) <= 1

    @pytest.mark.asyncio
    async def test_invalid_cursor_returns_400(self, client) -> None:
        headers = await _auth_header(client)
        response = await client.get(
            "/api/traders?cursor=not_valid_base64", headers=headers
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_cursor_pagination(self, client, db_session) -> None:
        await _seed_traders(db_session)
        headers = await _auth_header(client)
        r1 = await client.get("/api/traders?limit=1&period=week", headers=headers)
        assert r1.status_code == 200
        cursor = r1.json().get("next_cursor")
        if cursor:
            r2 = await client.get(
                f"/api/traders?limit=1&period=week&cursor={cursor}",
                headers=headers,
            )
            assert r2.status_code == 200


class TestTraderDetail:
    @pytest.mark.asyncio
    async def test_returns_trader_by_id(self, client, db_session) -> None:
        ids = await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get(f"/api/traders/{ids[0]}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == ids[0]
        assert "hl_address" in body

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_trader(self, client) -> None:
        headers = await _auth_header(client)
        response = await client.get("/api/traders/999999", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_equity_curve_returns_list(self, client, db_session) -> None:
        ids = await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get(
            f"/api/traders/{ids[0]}/equity-curve", headers=headers
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_positions_returns_list(self, client, db_session) -> None:
        ids = await _seed_traders(db_session)
        headers = await _auth_header(client)
        response = await client.get(f"/api/traders/{ids[0]}/positions", headers=headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)

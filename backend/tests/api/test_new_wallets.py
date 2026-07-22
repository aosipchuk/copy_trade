import hashlib
import hmac
import itertools
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.models.new_wallet import (
    NewWalletCandidate,
    NewWalletFundingLink,
    UserNewWalletItem,
    UserNewWalletSubscription,
)
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.services.portfolio.subscription_lifecycle import (
    executable_subscription_targets_for_signal,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_addr_counter: itertools.count = itertools.count(1)


@pytest.fixture(autouse=True)
async def _clean_new_wallet_test_tables(db_session) -> None:
    for model in (
        UserTrade,
        Signal,
        UserNewWalletItem,
        UserNewWalletSubscription,
        NewWalletFundingLink,
        NewWalletCandidate,
        Subscription,
        Trader,
    ):
        await db_session.execute(delete(model))
    await db_session.commit()


def _make_init_data(user_id: int, username: str = "newwallet") -> str:
    bot_token = "123456:test"
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "New"}
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


async def _auth(client, user_id: int) -> tuple[dict[str, str], int]:
    response = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, int(body["user_id"])


async def _seed_qualified_candidate(db_session) -> tuple[int, int]:
    index = next(_addr_counter)
    address = f"0x{index:040x}"
    trader = Trader(
        hl_address=address,
        display_name=None,
        is_active=True,
        has_perp_activity=None,
        last_seen_at=datetime(2026, 7, 20, 12, 0, 0),
    )
    db_session.add(trader)
    await db_session.flush()

    candidate = NewWalletCandidate(
        trader_id=trader.id,
        hl_address=trader.hl_address,
        status="qualified",
        detected_at=datetime(2026, 7, 20, 12, 0, 0),
        funded_at=datetime(2026, 7, 20, 12, 0, 0),
        qualified_at=datetime(2026, 7, 20, 12, 1, 0),
        chain_depth=1,
        chain_total_balance_usd=Decimal("16000"),
        threshold_usd_snapshot=Decimal("15000"),
    )
    db_session.add(candidate)
    await db_session.flush()
    db_session.add(
        NewWalletFundingLink(
            candidate_id=candidate.id,
            depth=1,
            wallet_address=trader.hl_address,
            funded_by_address="0x" + "11" * 20,
            amount_usdc=Decimal("500"),
            event_time=datetime(2026, 7, 20, 12, 0, 0),
            tx_hash="0xtest",
            balance_usd=Decimal("16000"),
            balance_source="test",
        )
    )
    await db_session.commit()
    return candidate.id, trader.id


class TestNewWalletsAPI:
    async def test_candidate_list_requires_auth(self, client) -> None:
        response = await client.get("/api/new-wallets/candidates")
        assert response.status_code == 401

    async def test_demo_activation_creates_child_for_existing_qualified_candidate(
        self, client, db_session, monkeypatch
    ) -> None:
        candidate_id, trader_id = await _seed_qualified_candidate(db_session)
        headers, _user_id = await _auth(client, 82001)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)

        response = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 5,
                "max_per_wallet_usd": 100,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == "active"
        assert payload["items"][0]["candidate_id"] == candidate_id
        assert payload["items"][0]["trader_id"] == trader_id

        child = await db_session.get(
            Subscription,
            payload["items"][0]["subscription_id"],
        )
        assert child is not None
        assert child.source_type == "new_wallet"
        assert child.expires_at is not None

    async def test_candidate_list_marks_existing_active_subscription(
        self, client, db_session
    ) -> None:
        candidate_id, trader_id = await _seed_qualified_candidate(db_session)
        headers, user_id = await _auth(client, 82020)
        existing = Subscription(
            user_id=user_id,
            trader_id=trader_id,
            max_allocation_usd=Decimal("125"),
            copy_ratio_pct=Decimal("100"),
            stop_loss_pct=Decimal("20"),
            max_leverage=Decimal("10"),
            sizing_mode="fixed_ratio",
            source_type="manual",
            is_active=True,
            is_demo=True,
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        response = await client.get("/api/new-wallets/candidates", headers=headers)

        assert response.status_code == 200, response.text
        item = next(
            item for item in response.json()["items"] if item["id"] == candidate_id
        )
        assert item["user_is_subscribed"] is True
        assert item["user_active_subscription_id"] == existing.id
        assert item["user_is_demo_subscribed"] is True
        assert item["user_demo_subscription_id"] == existing.id
        assert item["user_is_live_subscribed"] is False
        assert item["user_live_subscription_id"] is None

    async def test_candidate_list_prioritizes_existing_active_subscription(
        self, client, db_session
    ) -> None:
        subscribed_candidate_id, subscribed_trader_id = (
            await _seed_qualified_candidate(db_session)
        )
        newer_candidate_id, _newer_trader_id = await _seed_qualified_candidate(
            db_session
        )
        headers, user_id = await _auth(client, 82023)
        existing = Subscription(
            user_id=user_id,
            trader_id=subscribed_trader_id,
            max_allocation_usd=Decimal("125"),
            copy_ratio_pct=Decimal("100"),
            stop_loss_pct=Decimal("20"),
            max_leverage=Decimal("10"),
            sizing_mode="fixed_ratio",
            source_type="manual",
            is_active=True,
            is_demo=True,
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        response = await client.get(
            "/api/new-wallets/candidates",
            headers=headers,
            params={"limit": 1},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["id"] for item in body["items"]] == [subscribed_candidate_id]
        assert body["items"][0]["user_is_subscribed"] is True
        assert body["items"][0]["user_active_subscription_id"] == existing.id
        assert body["items"][0]["user_is_demo_subscribed"] is True
        assert body["items"][0]["user_is_live_subscribed"] is False
        assert body["next_cursor"] is not None

        next_response = await client.get(
            "/api/new-wallets/candidates",
            headers=headers,
            params={"limit": 1, "cursor": body["next_cursor"]},
        )

        assert next_response.status_code == 200, next_response.text
        assert [item["id"] for item in next_response.json()["items"]] == [
            newer_candidate_id
        ]

    async def test_subscribe_all_new_ignores_max_wallet_limit(
        self, client, db_session, monkeypatch
    ) -> None:
        first_candidate_id, _first_trader_id = await _seed_qualified_candidate(
            db_session
        )
        second_candidate_id, _second_trader_id = await _seed_qualified_candidate(
            db_session
        )
        headers, _user_id = await _auth(client, 82021)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)

        response = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 1,
                "subscribe_all_new": True,
                "max_per_wallet_usd": 75,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["subscribe_all_new"] is True
        assert {item["candidate_id"] for item in payload["items"]} == {
            first_candidate_id,
            second_candidate_id,
        }
        assert {item["target_allocation_usd"] for item in payload["items"]} == {75.0}

    async def test_subscribe_all_new_skips_existing_subscription_without_mutation(
        self, client, db_session, monkeypatch
    ) -> None:
        existing_candidate_id, existing_trader_id = await _seed_qualified_candidate(
            db_session
        )
        new_candidate_id, _new_trader_id = await _seed_qualified_candidate(db_session)
        headers, user_id = await _auth(client, 82022)
        existing = Subscription(
            user_id=user_id,
            trader_id=existing_trader_id,
            max_allocation_usd=Decimal("321"),
            copy_ratio_pct=Decimal("80"),
            stop_loss_pct=Decimal("25"),
            max_leverage=Decimal("7"),
            sizing_mode="fixed_ratio",
            source_type="manual",
            is_active=True,
            is_demo=True,
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)

        response = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 1,
                "subscribe_all_new": True,
                "max_per_wallet_usd": 90,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )

        assert response.status_code == 201, response.text
        item_candidate_ids = {item["candidate_id"] for item in response.json()["items"]}
        assert existing_candidate_id not in item_candidate_ids
        assert new_candidate_id in item_candidate_ids

        await db_session.refresh(existing)
        assert existing.is_active is True
        assert existing.max_allocation_usd == Decimal("321")
        assert existing.copy_ratio_pct == Decimal("80")
        duplicate_result = await db_session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.trader_id == existing_trader_id,
                Subscription.is_active.is_(True),
            )
        )
        active_subscription_ids = [
            subscription.id for subscription in duplicate_result.scalars().all()
        ]
        assert active_subscription_ids == [existing.id]

    async def test_manual_candidate_attach_requires_active_parent(
        self, client, db_session
    ) -> None:
        candidate_id, _trader_id = await _seed_qualified_candidate(db_session)
        headers, _user_id = await _auth(client, 82024)

        response = await client.post(
            f"/api/new-wallet-subscriptions/candidates/{candidate_id}",
            headers=headers,
            json={"is_demo": True},
        )

        assert response.status_code == 404
        assert "Active demo" in response.json()["detail"]

    async def test_manual_candidate_attach_ignores_parent_wallet_limit(
        self, client, db_session, monkeypatch
    ) -> None:
        first_candidate_id, _first_trader_id = await _seed_qualified_candidate(
            db_session
        )
        second_candidate_id, second_trader_id = await _seed_qualified_candidate(
            db_session
        )
        await db_session.execute(
            update(NewWalletCandidate)
            .where(NewWalletCandidate.id == first_candidate_id)
            .values(qualified_at=datetime(2026, 7, 20, 12, 0, 0))
        )
        await db_session.execute(
            update(NewWalletCandidate)
            .where(NewWalletCandidate.id == second_candidate_id)
            .values(qualified_at=datetime(2026, 7, 20, 12, 2, 0))
        )
        await db_session.commit()
        headers, user_id = await _auth(client, 82025)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)

        created = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 1,
                "max_per_wallet_usd": 75,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )
        assert created.status_code == 201, created.text
        parent_payload = created.json()
        assert parent_payload["max_active_wallets"] == 1
        assert [item["candidate_id"] for item in parent_payload["items"]] == [
            first_candidate_id
        ]

        response = await client.post(
            f"/api/new-wallet-subscriptions/candidates/{second_candidate_id}",
            headers=headers,
            json={"is_demo": True},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == second_candidate_id
        assert body["trader_id"] == second_trader_id
        assert body["user_is_subscribed"] is True
        assert body["user_item_status"] == "active"
        assert body["user_child_subscription_id"] is not None

        items_result = await db_session.execute(
            select(UserNewWalletItem).where(
                UserNewWalletItem.user_new_wallet_subscription_id
                == parent_payload["id"],
                UserNewWalletItem.status == "active",
            )
        )
        active_items = list(items_result.scalars().all())
        assert {item.candidate_id for item in active_items} == {
            first_candidate_id,
            second_candidate_id,
        }

        child = await db_session.get(Subscription, body["user_child_subscription_id"])
        assert child is not None
        assert child.user_id == user_id
        assert child.trader_id == second_trader_id
        assert child.source_type == "new_wallet"
        assert child.source_id == parent_payload["id"]
        assert child.is_demo is True
        assert child.max_allocation_usd == Decimal("75")

    async def test_manual_candidate_attach_allows_opposite_subscription_mode(
        self, client, db_session, monkeypatch
    ) -> None:
        candidate_id, trader_id = await _seed_qualified_candidate(db_session)
        headers, user_id = await _auth(client, 82026)
        existing_live = Subscription(
            user_id=user_id,
            trader_id=trader_id,
            max_allocation_usd=Decimal("125"),
            copy_ratio_pct=Decimal("100"),
            stop_loss_pct=Decimal("20"),
            max_leverage=Decimal("10"),
            sizing_mode="fixed_ratio",
            source_type="manual",
            is_active=True,
            is_demo=False,
        )
        db_session.add(existing_live)
        await db_session.commit()
        await db_session.refresh(existing_live)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", False)

        created = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 1,
                "max_per_wallet_usd": 75,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["items"] == []

        response = await client.post(
            f"/api/new-wallet-subscriptions/candidates/{candidate_id}",
            headers=headers,
            json={"is_demo": True},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_is_live_subscribed"] is True
        assert body["user_live_subscription_id"] == existing_live.id
        assert body["user_is_demo_subscribed"] is True
        assert body["user_demo_subscription_id"] == body["user_child_subscription_id"]

    async def test_live_activation_requires_wallet_and_agent(
        self, client, db_session, monkeypatch
    ) -> None:
        await _seed_qualified_candidate(db_session)
        headers, _user_id = await _auth(client, 82002)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)

        response = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": False,
                "total_allocation_usd": 500,
                "max_active_wallets": 5,
                "max_per_wallet_usd": 100,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
                "risk_disclosure_accepted": True,
            },
        )

        assert response.status_code == 400
        assert "HL wallet" in response.json()["detail"]

    async def test_cancel_deactivates_generated_subscriptions(
        self, client, db_session, monkeypatch
    ) -> None:
        await _seed_qualified_candidate(db_session)
        headers, _user_id = await _auth(client, 82003)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)
        created = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 5,
                "max_per_wallet_usd": 100,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )
        assert created.status_code == 201, created.text
        parent_id = created.json()["id"]
        child_id = created.json()["items"][0]["subscription_id"]

        response = await client.delete(
            f"/api/new-wallet-subscriptions/{parent_id}",
            headers=headers,
            params={"close_positions": True},
        )

        assert response.status_code == 200, response.text
        child = await db_session.get(Subscription, child_id)
        assert child is not None
        assert child.is_active is False
        assert child.ended_reason == "new_wallet_parent_canceled"

    async def test_expired_child_no_longer_executes(
        self, client, db_session, monkeypatch
    ) -> None:
        await _seed_qualified_candidate(db_session)
        headers, _user_id = await _auth(client, 82004)
        monkeypatch.setattr(settings, "new_wallet_discovery_enabled", True)
        monkeypatch.setattr(settings, "new_wallet_auto_attach_enabled", True)
        created = await client.post(
            "/api/new-wallet-subscriptions",
            headers=headers,
            json={
                "is_demo": True,
                "total_allocation_usd": 500,
                "max_active_wallets": 5,
                "max_per_wallet_usd": 100,
                "copy_ratio_pct": 100,
                "stop_loss_pct": 20,
                "max_leverage": 10,
                "sizing_mode": "fixed_ratio",
                "close_positions_on_expire": True,
            },
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["items"][0]["id"]
        child_id = created.json()["items"][0]["subscription_id"]
        trader_id = created.json()["items"][0]["trader_id"]

        await db_session.execute(
            update(Subscription)
            .where(Subscription.id == child_id)
            .values(
                expires_at=datetime.now(tz=UTC).replace(tzinfo=None)
                - timedelta(seconds=1)
            )
        )
        signal = Signal(
            trader_id=trader_id,
            signal_type="OPEN",
            coin="BTC",
            side="long",
            size=Decimal("0.01"),
        )
        db_session.add(signal)
        await db_session.commit()

        targets = await executable_subscription_targets_for_signal(
            db_session,
            signal.id,
        )

        assert targets == []
        item = await db_session.get(UserNewWalletItem, item_id)
        assert item is not None
        assert item.subscription_id == child_id

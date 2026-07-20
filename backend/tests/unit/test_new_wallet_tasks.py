from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.new_wallet import UserNewWalletItem, UserNewWalletSubscription
from app.models.subscription import Subscription
from app.tasks.new_wallets import expire_new_wallet_subscriptions_async


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeResult(self.rows))
        return db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_expiry_deactivates_before_demo_close(monkeypatch) -> None:
    item = UserNewWalletItem(
        user_new_wallet_subscription_id=1,
        candidate_id=1,
        subscription_id=10,
        trader_id=20,
        target_allocation_usd=Decimal("100"),
        status="active",
        expires_at=datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(seconds=1),
    )
    subscription = Subscription(
        id=10,
        user_id=5,
        trader_id=20,
        max_allocation_usd=Decimal("100"),
        source_type="new_wallet",
        is_demo=True,
        is_active=True,
    )
    parent = UserNewWalletSubscription(
        id=1,
        user_id=5,
        status="active",
        is_demo=True,
        total_allocation_usd=Decimal("500"),
        max_active_wallets=5,
        max_per_wallet_usd=Decimal("100"),
        close_positions_on_expire=True,
    )

    monkeypatch.setattr(
        "app.tasks.new_wallets.get_db_session",
        lambda: FakeSession([(item, subscription, parent)]),
    )
    close_mock = AsyncMock(return_value=0)
    with patch(
        "app.services.demo_service.close_demo_subscription_positions",
        close_mock,
    ):
        await expire_new_wallet_subscriptions_async()

    assert subscription.is_active is False
    assert subscription.ended_reason == "new_wallet_ttl_expired"
    assert item.status == "expired"
    close_mock.assert_awaited_once()

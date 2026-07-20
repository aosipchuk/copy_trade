from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hyperliquid.models import Position, PositionLeverage
from app.tasks import hl_tracker


def _pos() -> Position:
    return Position(
        coin="BTC",
        szi=Decimal("0.01"),
        entryPx=Decimal("60000"),
        unrealizedPnl=Decimal("0"),
        leverage=PositionLeverage(type="cross", value=10),
    )


class FakeRedis:
    def __init__(self, raw: str | None) -> None:
        self.raw = raw
        self.saved: str | None = None

    def get(self, key: str) -> str | None:
        return self.raw

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.saved = value


class FakeSession:
    async def __aenter__(self):
        trader = MagicMock()
        trader.id = 1
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=trader))
        )
        return db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_empty_previous_snapshot_detects_first_open(monkeypatch) -> None:
    redis = FakeRedis("[]")
    monkeypatch.setattr(hl_tracker, "get_redis_client", lambda: redis)
    monkeypatch.setattr(hl_tracker, "get_db_session", lambda: FakeSession())

    with (
        patch.object(
            hl_tracker.HyperliquidInfoClient,
            "get_positions",
            AsyncMock(return_value=[_pos()]),
        ),
        patch.object(hl_tracker, "save_signals", AsyncMock(return_value=[])) as saved,
    ):
        count = await hl_tracker._poll_trader_positions_async("0x" + "aa" * 20)

    assert count == 0
    saved.assert_called_once()
    assert redis.saved is not None


@pytest.mark.asyncio
async def test_missing_previous_snapshot_only_sets_baseline(monkeypatch) -> None:
    redis = FakeRedis(None)
    monkeypatch.setattr(hl_tracker, "get_redis_client", lambda: redis)

    with (
        patch.object(
            hl_tracker.HyperliquidInfoClient,
            "get_positions",
            AsyncMock(return_value=[_pos()]),
        ),
        patch.object(hl_tracker, "save_signals", AsyncMock(return_value=[])) as saved,
    ):
        count = await hl_tracker._poll_trader_positions_async("0x" + "aa" * 20)

    assert count == 0
    saved.assert_not_called()
    assert redis.saved is not None

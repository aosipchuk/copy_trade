"""Unit tests for demo_executor — paper-trade simulation logic."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.copy_engine.demo_executor import (
    _find_open_demo_trade,
    _handle_demo_close,
    _handle_demo_open,
)
from app.services.hyperliquid.models import AssetMeta, Meta


def _make_signal(
    signal_type: str = "OPEN",
    coin: str = "BTC",
    side: str = "long",
    size: float = 0.1,
    trader_id: int = 1,
    signal_id: int = 10,
) -> MagicMock:
    sig = MagicMock()
    sig.id = signal_id
    sig.trader_id = trader_id
    sig.signal_type = signal_type
    sig.coin = coin
    sig.side = side
    sig.size = size
    return sig


def _make_subscription(
    sub_id: int = 1,
    max_allocation_usd: float = 1000.0,
    copy_ratio_pct: float = 100.0,
    stop_loss_pct: float = 20.0,
    max_leverage: float = 20.0,
    sizing_mode: str = "fixed_usd",
    max_per_coin_usd: float | None = None,
    allowed_coins: list[str] | None = None,
) -> MagicMock:
    sub = MagicMock()
    sub.id = sub_id
    sub.max_allocation_usd = max_allocation_usd
    sub.copy_ratio_pct = copy_ratio_pct
    sub.stop_loss_pct = stop_loss_pct
    sub.max_leverage = max_leverage
    sub.sizing_mode = sizing_mode
    sub.max_per_coin_usd = max_per_coin_usd
    sub.allowed_coins = allowed_coins
    return sub


def _make_meta(coin: str = "BTC", sz_decimals: int = 3, max_leverage: int = 40) -> Meta:
    return Meta(
        universe=[AssetMeta(name=coin, szDecimals=sz_decimals, maxLeverage=max_leverage)]
    )


def _make_db() -> AsyncMock:
    """AsyncMock db where add() is synchronous (matches SQLAlchemy session.add)."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_open_trade(
    price: float = 50000.0,
    size: float = 0.01,
    side: str = "long",
    coin: str = "BTC",
    sub_id: int = 1,
) -> MagicMock:
    trade = MagicMock()
    trade.price = price
    trade.size = size
    trade.side = side
    trade.coin = coin
    trade.subscription_id = sub_id
    return trade


class TestDemoExecutorOpen:
    @pytest.mark.asyncio
    async def test_open_saves_trade(self) -> None:
        """OPEN signal saves a filled demo UserTrade at exact mid price."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "long")
        sub = _make_subscription(max_allocation_usd=1000.0, sizing_mode="fixed_usd")
        mid_price = Decimal("50000")
        meta = _make_meta("BTC")

        await _handle_demo_open(db, signal, sub, mid_price, meta)

        db.add.assert_called_once()
        trade = db.add.call_args[0][0]
        assert trade.is_demo is True
        assert trade.status == "filled"
        assert trade.trade_type == "open"
        assert trade.coin == "BTC"
        assert trade.price == pytest.approx(float(mid_price))

    @pytest.mark.asyncio
    async def test_open_long_side(self) -> None:
        """OPEN for long signal → trade.side == 'long'."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "long")
        sub = _make_subscription(max_allocation_usd=1000.0, sizing_mode="fixed_usd")
        meta = _make_meta("BTC")

        await _handle_demo_open(db, signal, sub, Decimal("50000"), meta)

        trade = db.add.call_args[0][0]
        assert trade.side == "long"

    @pytest.mark.asyncio
    async def test_open_short_side(self) -> None:
        """OPEN for short signal → trade.side == 'short'."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "short")
        sub = _make_subscription(max_allocation_usd=1000.0, sizing_mode="fixed_usd")
        meta = _make_meta("BTC")

        await _handle_demo_open(db, signal, sub, Decimal("50000"), meta)

        db.add.assert_called_once()
        trade = db.add.call_args[0][0]
        assert trade.side == "short"

    @pytest.mark.asyncio
    async def test_open_below_min_skips(self) -> None:
        """OPEN with notional below MIN_TRADE_USD (10 USD): no trade added."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "long")
        # fixed_usd sizing: notional = max_allocation_usd = 5 < MIN_TRADE_USD
        sub = _make_subscription(max_allocation_usd=5.0, sizing_mode="fixed_usd")
        meta = _make_meta("BTC")

        await _handle_demo_open(db, signal, sub, Decimal("50000"), meta)

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_unknown_coin_in_meta_skips(self) -> None:
        """Coin not present in meta.universe → no trade added (guard before signal_to_order)."""
        db = _make_db()
        signal = _make_signal("OPEN", "ETH", "long")
        sub = _make_subscription(max_allocation_usd=1000.0)
        meta = _make_meta("BTC")  # only BTC, not ETH

        await _handle_demo_open(db, signal, sub, Decimal("3000"), meta)

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_size_computed_from_notional_and_mid(self) -> None:
        """Trade size = max_allocation_usd / mid_price (rounded to sz_decimals)."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "long")
        sub = _make_subscription(max_allocation_usd=1000.0, sizing_mode="fixed_usd")
        meta = _make_meta("BTC", sz_decimals=3)
        mid_price = Decimal("50000")

        await _handle_demo_open(db, signal, sub, mid_price, meta)

        trade = db.add.call_args[0][0]
        # 1000 / 50000 = 0.02 → quantized to 3 decimal places = 0.020
        assert trade.size == pytest.approx(0.020)

    @pytest.mark.asyncio
    async def test_open_records_subscription_and_signal_ids(self) -> None:
        """Trade references the correct subscription_id and signal_id."""
        db = _make_db()
        signal = _make_signal("OPEN", "BTC", "long", signal_id=42)
        sub = _make_subscription(sub_id=7, max_allocation_usd=500.0, sizing_mode="fixed_usd")
        meta = _make_meta("BTC")

        await _handle_demo_open(db, signal, sub, Decimal("50000"), meta)

        trade = db.add.call_args[0][0]
        assert trade.subscription_id == 7
        assert trade.signal_id == 42


class TestDemoExecutorClose:
    @pytest.mark.asyncio
    async def test_close_computes_pnl_long(self) -> None:
        """CLOSE for long: realized_pnl = (exit - entry) * size."""
        db = _make_db()
        signal = _make_signal("CLOSE", "BTC", "long")
        sub = _make_subscription()
        mid_price = Decimal("55000")
        open_trade = _make_open_trade(price=50000.0, size=0.01, side="long")

        with patch(
            "app.services.copy_engine.demo_executor._find_open_demo_trade",
            AsyncMock(return_value=open_trade),
        ):
            await _handle_demo_close(db, signal, sub, mid_price)

        db.add.assert_called_once()
        trade = db.add.call_args[0][0]
        # (55000 - 50000) * 0.01 * 1 = 50.0
        assert trade.realized_pnl == pytest.approx(50.0)
        assert trade.is_demo is True
        assert trade.status == "filled"
        assert trade.trade_type == "close"

    @pytest.mark.asyncio
    async def test_close_computes_pnl_short(self) -> None:
        """CLOSE for short: realized_pnl = (entry - exit) * size (direction = -1)."""
        db = _make_db()
        signal = _make_signal("CLOSE", "BTC", "short")
        sub = _make_subscription()
        mid_price = Decimal("45000")  # price dropped → profit for short
        open_trade = _make_open_trade(price=50000.0, size=0.01, side="short")

        with patch(
            "app.services.copy_engine.demo_executor._find_open_demo_trade",
            AsyncMock(return_value=open_trade),
        ):
            await _handle_demo_close(db, signal, sub, mid_price)

        trade = db.add.call_args[0][0]
        # (45000 - 50000) * 0.01 * -1 = 50.0
        assert trade.realized_pnl == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_close_long_loss(self) -> None:
        """CLOSE for long when price dropped → negative realized_pnl."""
        db = _make_db()
        signal = _make_signal("CLOSE", "BTC", "long")
        sub = _make_subscription()
        mid_price = Decimal("45000")
        open_trade = _make_open_trade(price=50000.0, size=0.02, side="long")

        with patch(
            "app.services.copy_engine.demo_executor._find_open_demo_trade",
            AsyncMock(return_value=open_trade),
        ):
            await _handle_demo_close(db, signal, sub, mid_price)

        trade = db.add.call_args[0][0]
        # (45000 - 50000) * 0.02 * 1 = -100.0
        assert trade.realized_pnl == pytest.approx(-100.0)

    @pytest.mark.asyncio
    async def test_close_no_open_trade_noop(self) -> None:
        """CLOSE with no matching open demo trade → no UserTrade added."""
        db = _make_db()
        signal = _make_signal("CLOSE", "BTC", "long")
        sub = _make_subscription()

        with patch(
            "app.services.copy_engine.demo_executor._find_open_demo_trade",
            AsyncMock(return_value=None),
        ):
            await _handle_demo_close(db, signal, sub, Decimal("55000"))

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_uses_open_trade_price_as_entry(self) -> None:
        """Close trade price reflects exit mid, not entry price of open trade."""
        db = _make_db()
        signal = _make_signal("CLOSE", "BTC", "long")
        sub = _make_subscription()
        exit_price = Decimal("60000")
        open_trade = _make_open_trade(price=40000.0, size=0.005, side="long")

        with patch(
            "app.services.copy_engine.demo_executor._find_open_demo_trade",
            AsyncMock(return_value=open_trade),
        ):
            await _handle_demo_close(db, signal, sub, exit_price)

        trade = db.add.call_args[0][0]
        assert trade.price == pytest.approx(float(exit_price))
        # (60000 - 40000) * 0.005 * 1 = 100.0
        assert trade.realized_pnl == pytest.approx(100.0)


class TestFindOpenDemoTrade:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_matching_trade(self) -> None:
        """No open demo trade for coin → returns None."""
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=None)
            )
        )

        result = await _find_open_demo_trade(db, subscription_id=1, coin="BTC")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_trade_when_found(self) -> None:
        """Matching open demo trade found → returns it."""
        mock_trade = MagicMock()
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_trade)
            )
        )

        result = await _find_open_demo_trade(db, subscription_id=5, coin="ETH")

        assert result is mock_trade

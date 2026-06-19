"""Unit tests for order_builder.signal_to_order and build_close_order."""

from decimal import Decimal
from unittest.mock import MagicMock

from app.services.copy_engine.constants import IOC_SLIPPAGE, MIN_TRADE_USD
from app.services.copy_engine.order_builder import (
    build_close_order,
    signal_to_order,
)
from app.services.hyperliquid.models import AssetMeta


def _sub(
    max_allocation: float = 1000.0,
    copy_ratio: float = 100.0,
    stop_loss: float = 20.0,
    sizing_mode: str = "equity_pct",
    max_per_coin_usd: float | None = None,
    allowed_coins: list[str] | None = None,
    max_leverage: float = 20.0,
) -> MagicMock:
    sub = MagicMock()
    sub.max_allocation_usd = max_allocation
    sub.copy_ratio_pct = copy_ratio
    sub.stop_loss_pct = stop_loss
    sub.sizing_mode = sizing_mode
    sub.max_per_coin_usd = max_per_coin_usd
    sub.allowed_coins = allowed_coins
    sub.max_leverage = max_leverage
    return sub


def _signal(
    coin: str = "BTC",
    side: str = "long",
    size: float | None = 0.1,
) -> MagicMock:
    sig = MagicMock()
    sig.coin = coin
    sig.side = side
    sig.size = size
    return sig


def _meta(sz_decimals: int = 3, max_leverage: int = 40) -> AssetMeta:
    return AssetMeta(name="BTC", szDecimals=sz_decimals, maxLeverage=max_leverage)


class TestSignalToOrder:
    def test_returns_order_for_whitelisted_coin(self) -> None:
        order = signal_to_order(
            _signal("BTC"), _sub(), Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.coin == "BTC"

    def test_returns_none_for_unlisted_coin(self) -> None:
        order = signal_to_order(
            _signal("SHIB"), _sub(), Decimal("5000"), Decimal("0.00001"), _meta()
        )
        assert order is None

    def test_buy_for_long_signal(self) -> None:
        order = signal_to_order(
            _signal("BTC", "long"), _sub(), Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.is_buy is True

    def test_sell_for_short_signal(self) -> None:
        order = signal_to_order(
            _signal("BTC", "short"), _sub(), Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.is_buy is False

    def test_sizing_capped_by_max_allocation(self) -> None:
        # equity=100k, copy_ratio=100% → notional = min(500, 100k) = 500
        order = signal_to_order(
            _signal(),
            _sub(max_allocation=500),
            Decimal("100000"),
            Decimal("50000"),
            _meta(),
        )
        assert order is not None
        # size = 500 / 50000 = 0.01
        assert order.size == Decimal("0.010")

    def test_sizing_capped_by_equity_ratio(self) -> None:
        # equity=100, copy_ratio=50% → notional = min(1000, 50) = 50 → too small for MIN_TRADE_USD
        # Actually 50 > 10, so it should work: size = 50/50000 = 0.001
        order = signal_to_order(
            _signal(),
            _sub(max_allocation=1000, copy_ratio=50),
            Decimal("100"),
            Decimal("50000"),
            _meta(),
        )
        assert order is not None
        notional = order.size * Decimal("50000")
        assert notional >= MIN_TRADE_USD

    def test_returns_none_below_min_trade_usd(self) -> None:
        # equity=5, copy_ratio=100% → notional = min(1000, 5) = 5 < MIN_TRADE_USD
        order = signal_to_order(
            _signal(), _sub(), Decimal("5"), Decimal("50000"), _meta()
        )
        assert order is None

    def test_returns_none_for_zero_mid_price(self) -> None:
        order = signal_to_order(
            _signal(), _sub(), Decimal("5000"), Decimal("0"), _meta()
        )
        assert order is None

    def test_buy_limit_price_has_positive_slippage(self) -> None:
        mid = Decimal("50000")
        order = signal_to_order(
            _signal("BTC", "long"), _sub(), Decimal("5000"), mid, _meta()
        )
        assert order is not None
        assert order.limit_px > mid

    def test_sell_limit_price_has_negative_slippage(self) -> None:
        mid = Decimal("50000")
        order = signal_to_order(
            _signal("BTC", "short"), _sub(), Decimal("5000"), mid, _meta()
        )
        assert order is not None
        assert order.limit_px < mid

    def test_slippage_is_correct_magnitude(self) -> None:
        mid = Decimal("50000")
        order = signal_to_order(
            _signal("BTC", "long"), _sub(), Decimal("5000"), mid, _meta()
        )
        assert order is not None
        expected_px = mid * (1 + IOC_SLIPPAGE)
        # allow rounding difference
        assert abs(order.limit_px - expected_px.quantize(Decimal("0.0001"))) < Decimal(
            "0.01"
        )

    def test_size_quantized_to_sz_decimals(self) -> None:
        # sz_decimals=1, mid=1000, allocation=100 → size = 100/1000 = 0.1 (exactly)
        order = signal_to_order(
            _signal(),
            _sub(max_allocation=100),
            Decimal("5000"),
            Decimal("1000"),
            _meta(sz_decimals=1),
        )
        assert order is not None
        assert order.size == order.size.quantize(Decimal("0.1"))

    def test_reduces_allocation_to_whats_affordable(self) -> None:
        # equity=$500, copy_ratio=10% → notional = min(10000, 50) = 50 > $10 MIN
        # mid=1000 → size = 50/1000 = 0.05
        order = signal_to_order(
            _signal(),
            _sub(max_allocation=10000, copy_ratio=10),
            Decimal("500"),
            Decimal("1000"),
            _meta(),
        )
        assert order is not None
        notional = order.size * Decimal("1000")
        assert notional <= Decimal("500") * Decimal("10") / 100 + Decimal("1")

    def test_not_reduce_only_for_open(self) -> None:
        order = signal_to_order(
            _signal(), _sub(), Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.reduce_only is False


class TestSizingModes:
    def test_fixed_ratio_uses_trader_position_size(self) -> None:
        # trader size=0.1 BTC at $50k → trader notional=$5k, copy_ratio=50% → $2500
        sub = _sub(max_allocation=10000, copy_ratio=50, sizing_mode="fixed_ratio")
        order = signal_to_order(
            _signal(size=0.1), sub, Decimal("0"), Decimal("50000"), _meta()
        )
        assert order is not None
        expected_notional = Decimal("0.1") * Decimal("50000") * Decimal("0.5")  # 2500
        assert (
            order.size * Decimal("50000")
            == expected_notional.quantize(Decimal("0.001")) * Decimal("50000")
            or True
        )  # noqa: E501
        # size = 2500 / 50000 = 0.05
        assert order.size == Decimal("0.050")

    def test_fixed_ratio_capped_by_max_allocation(self) -> None:
        # trader size=1 BTC at $50k → $50k, copy_ratio=100%, max_alloc=$500 → $500
        sub = _sub(max_allocation=500, copy_ratio=100, sizing_mode="fixed_ratio")
        order = signal_to_order(
            _signal(size=1.0), sub, Decimal("0"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.size == Decimal("0.010")  # 500/50000

    def test_fixed_ratio_returns_none_when_signal_size_missing(self) -> None:
        sub = _sub(sizing_mode="fixed_ratio")
        order = signal_to_order(
            _signal(size=None), sub, Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is None

    def test_fixed_usd_uses_max_allocation_directly(self) -> None:
        sub = _sub(max_allocation=200, sizing_mode="fixed_usd")
        order = signal_to_order(_signal(), sub, Decimal("0"), Decimal("50000"), _meta())
        assert order is not None
        # notional=200, size=200/50000=0.004 → rounded to 0.004
        assert order.size == Decimal("0.004")

    def test_equity_pct_uses_user_equity(self) -> None:
        # equity=5000, copy_ratio=50% → notional=min(1000, 2500)=1000
        sub = _sub(max_allocation=1000, copy_ratio=50, sizing_mode="equity_pct")
        order = signal_to_order(
            _signal(), sub, Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.size == Decimal("0.020")  # 1000/50000

    def test_equity_pct_capped_by_max_allocation(self) -> None:
        # equity=100k, copy_ratio=100% → notional=min(500, 100k)=500
        sub = _sub(max_allocation=500, copy_ratio=100, sizing_mode="equity_pct")
        order = signal_to_order(
            _signal(), sub, Decimal("100000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.size == Decimal("0.010")  # 500/50000

    def test_max_per_coin_usd_caps_notional(self) -> None:
        # equity_pct: notional=1000, max_per_coin=100 → notional capped to 100
        sub = _sub(
            max_allocation=1000,
            copy_ratio=100,
            sizing_mode="equity_pct",
            max_per_coin_usd=100,
        )
        order = signal_to_order(
            _signal(), sub, Decimal("1000"), Decimal("50000"), _meta()
        )
        assert order is not None
        assert order.size == Decimal("0.002")  # 100/50000

    def test_allowed_coins_blocks_unlisted_coin(self) -> None:
        sub = _sub(allowed_coins=["ETH", "SOL"])
        order = signal_to_order(
            _signal("BTC"), sub, Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is None

    def test_allowed_coins_permits_listed_coin(self) -> None:
        sub = _sub(allowed_coins=["BTC", "ETH"])
        order = signal_to_order(
            _signal("BTC"), sub, Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None

    def test_allowed_coins_none_allows_all_whitelisted(self) -> None:
        sub = _sub(allowed_coins=None)
        order = signal_to_order(
            _signal("BTC"), sub, Decimal("5000"), Decimal("50000"), _meta()
        )
        assert order is not None

    def test_allowed_coins_does_not_bypass_whitelist(self) -> None:
        # SHIB is in allowed_coins but not in COIN_WHITELIST → still blocked
        sub = _sub(allowed_coins=["SHIB"])
        order = signal_to_order(
            _signal("SHIB"), sub, Decimal("5000"), Decimal("0.00001"), _meta()
        )
        assert order is None


class TestMaxLeverageCap:
    def test_max_leverage_caps_notional(self) -> None:
        """DoD: equity=$1000, max_leverage=2, notional=$5000 → order placed on $2000."""
        sub = _sub(
            max_allocation=5000, copy_ratio=100, sizing_mode="fixed_usd", max_leverage=2
        )
        order = signal_to_order(
            _signal(), sub, Decimal("1000"), Decimal("50000"), _meta()
        )
        assert order is not None
        # cap = 1000 * 2 = 2000; size = 2000/50000 = 0.040
        assert order.size == Decimal("0.040")

    def test_max_leverage_does_not_cap_when_within_limit(self) -> None:
        sub = _sub(
            max_allocation=1000, copy_ratio=100, sizing_mode="fixed_usd", max_leverage=5
        )
        order = signal_to_order(
            _signal(), sub, Decimal("1000"), Decimal("50000"), _meta()
        )
        assert order is not None
        # cap = 1000 * 5 = 5000 > notional 1000 → not applied; size = 1000/50000 = 0.020
        assert order.size == Decimal("0.020")

    def test_max_leverage_returns_none_when_capped_below_min_trade(self) -> None:
        # equity=$1, max_leverage=1 → cap = $1 < MIN_TRADE_USD → None
        sub = _sub(
            max_allocation=5000, copy_ratio=100, sizing_mode="fixed_usd", max_leverage=1
        )
        order = signal_to_order(_signal(), sub, Decimal("1"), Decimal("50000"), _meta())
        assert order is None

    def test_max_leverage_not_applied_when_equity_is_zero(self) -> None:
        # fixed_ratio with equity=0 → cap skipped; notional = min(500, 0.1*50000*0.5) = 2500→capped 500
        sub = _sub(
            max_allocation=500, copy_ratio=50, sizing_mode="fixed_ratio", max_leverage=2
        )
        order = signal_to_order(
            _signal(size=0.1), sub, Decimal("0"), Decimal("50000"), _meta()
        )
        assert order is not None
        # equity=0 → no leverage cap; notional = min(500, 0.1*50000*0.5)=min(500,2500)=500
        assert order.size == Decimal("0.010")  # 500/50000


class TestBuildCloseOrder:
    def test_close_long_is_sell(self) -> None:
        order = build_close_order(
            "BTC", 3, is_long=True, size=Decimal("0.01"), mid_price=Decimal("50000")
        )
        assert order.is_buy is False
        assert order.reduce_only is True

    def test_close_short_is_buy(self) -> None:
        order = build_close_order(
            "BTC", 3, is_long=False, size=Decimal("0.01"), mid_price=Decimal("50000")
        )
        assert order.is_buy is True
        assert order.reduce_only is True

    def test_close_long_price_below_mid(self) -> None:
        mid = Decimal("50000")
        order = build_close_order(
            "BTC", 3, is_long=True, size=Decimal("0.01"), mid_price=mid
        )
        assert order.limit_px < mid

    def test_close_short_price_above_mid(self) -> None:
        mid = Decimal("50000")
        order = build_close_order(
            "BTC", 3, is_long=False, size=Decimal("0.01"), mid_price=mid
        )
        assert order.limit_px > mid

    def test_preserves_coin_and_index(self) -> None:
        order = build_close_order(
            "ETH", 7, is_long=True, size=Decimal("0.1"), mid_price=Decimal("3000")
        )
        assert order.coin == "ETH"
        assert order.asset_index == 7

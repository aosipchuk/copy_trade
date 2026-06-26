from decimal import Decimal
from unittest.mock import patch

from app.services.analytics import metrics
from app.services.analytics.metrics import (
    _build_equity_curve_from_fills,
    _realized_pnl_for_period,
    compute_trader_quality_metrics,
)
from app.services.hyperliquid.models import Fill


def _fill(
    dir_: str,
    oid: int = 1,
    closed_pnl: Decimal = Decimal("5"),
    fee: Decimal = Decimal("0"),
) -> Fill:
    return Fill(
        coin="ETH",
        px=Decimal("2000"),
        sz=Decimal("1"),
        side="B",
        time=1_700_000_000_000 + oid,
        closedPnl=closed_pnl,
        dir=dir_,
        oid=oid,
        fee=fee,
    )


class _FakeClient:
    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills

    def __call__(self, *args: object, **kwargs: object) -> "_FakeClient":
        return self

    async def get_fills(self, address: str, limit: int | None = 50) -> list[Fill]:
        return self._fills


class TestHasPerpActivity:
    async def _compute(self, fills: list[Fill]) -> object:
        fake = _FakeClient(fills)
        with (
            patch.object(metrics, "HyperliquidInfoClient", fake),
            patch.object(metrics, "_redis_avg_leverage", return_value=None),
        ):
            return await compute_trader_quality_metrics("0xabc")

    async def test_perp_trader_flagged_true(self) -> None:
        fills = [
            _fill("Open Long", oid=1),
            _fill("Close Long", oid=1),
            _fill("Open Short", oid=2),
            _fill("Close Short", oid=2),
        ]
        result = await self._compute(fills)
        assert result is not None
        assert result.has_perp_activity is True

    async def test_prediction_market_trader_flagged_false(self) -> None:
        # Same dir values seen on the real non-copyable trader 0x4b34…
        fills = [
            _fill("Sell", oid=1),
            _fill("Negate Outcome", oid=2),
            _fill("Split Outcome", oid=3),
            _fill("Merge Question", oid=4),
        ]
        result = await self._compute(fills)
        assert result is not None
        assert result.has_perp_activity is False

    async def test_position_flip_counts_as_perp(self) -> None:
        result = await self._compute([_fill("Long > Short", oid=1)])
        assert result is not None
        assert result.has_perp_activity is True

    async def test_perp_period_stats_alltime(self) -> None:
        fills = [
            _fill("Open Long", oid=1, closed_pnl=Decimal("0")),  # notional 2000
            _fill("Close Long", oid=1, closed_pnl=Decimal("10")),  # notional 2000
            _fill("Negate Outcome", oid=2, closed_pnl=Decimal("999")),  # excluded
        ]
        result = await self._compute(fills)
        assert result is not None
        pnl, vol = result.perp_period_stats["allTime"]
        assert pnl == 10.0  # only perp closed_pnl
        assert vol == 4000.0  # 2 perp fills * 2000; prediction-market excluded

    async def test_fees_exclude_non_perp_fills(self) -> None:
        fills = [
            _fill("Close Long", oid=1, fee=Decimal("1")),
            _fill("Negate Outcome", oid=2, fee=Decimal("99")),  # prediction market
            _fill("Sell", oid=3, fee=Decimal("50")),  # spot
        ]
        result = await self._compute(fills)
        assert result is not None
        assert result.fees_paid_usd == 1.0  # only the perp fee counts


class TestPerpOnlyAnalytics:
    """Non-perp fills must not leak into realized PnL or the equity curve."""

    def test_realized_pnl_ignores_non_perp(self) -> None:
        fills = [
            _fill("Close Long", oid=1, closed_pnl=Decimal("10")),
            _fill("Negate Outcome", oid=2, closed_pnl=Decimal("999")),
            _fill("Sell", oid=3, closed_pnl=Decimal("500")),
        ]
        assert _realized_pnl_for_period(fills, "allTime") == 10.0

    def test_equity_curve_ignores_non_perp(self) -> None:
        fills = [
            _fill("Close Long", oid=1, closed_pnl=Decimal("10")),
            _fill("Negate Outcome", oid=2, closed_pnl=Decimal("999")),
        ]
        curve = _build_equity_curve_from_fills(fills, "allTime")
        assert [p.pnl for p in curve] == [10.0]

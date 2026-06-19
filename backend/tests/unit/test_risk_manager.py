"""Unit tests for risk_manager — stop-loss and portfolio risk checks."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.copy_engine.constants import MAX_ALLOCATION_EQUITY_FRACTION
from app.services.hyperliquid.models import MarginSummary
from app.services.risk_manager import (
    check_portfolio_risk,
    check_portfolio_stop_loss,
    check_subscription_stop_loss,
)


def _active_sub(
    sub_id: int = 1,
    stop_loss_pct: float = 20.0,
    max_allocation_usd: float = 1000.0,
) -> MagicMock:
    sub = MagicMock()
    sub.id = sub_id
    sub.is_active = True
    sub.stop_loss_pct = stop_loss_pct
    sub.max_allocation_usd = max_allocation_usd
    return sub


class TestCheckSubscriptionStopLoss:
    @pytest.mark.asyncio
    async def test_returns_false_when_subscription_not_found(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        result = await check_subscription_stop_loss(db, subscription_id=99)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_subscription_inactive(self) -> None:
        sub = _active_sub()
        sub.is_active = False
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sub))
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_trades(self) -> None:
        sub = _active_sub(stop_loss_pct=20.0, max_allocation_usd=1000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("0"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_loss_below_threshold(self) -> None:
        # threshold = 20% of $1000 = $200; loss = $100 → no trigger
        sub = _active_sub(stop_loss_pct=20.0, max_allocation_usd=1000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("-100"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_loss_exceeds_threshold(self) -> None:
        # threshold = 20% of $1000 = $200; loss = $250 → triggers
        sub = _active_sub(stop_loss_pct=20.0, max_allocation_usd=1000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("-250"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_loss_exactly_at_threshold(self) -> None:
        # threshold = 20% of $1000 = $200; loss = exactly $200 → triggers
        sub = _active_sub(stop_loss_pct=20.0, max_allocation_usd=1000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("-200"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_loss_amount_calculation(self) -> None:
        # threshold = 10% of $500 = $50; loss = $60 → triggers
        sub = _active_sub(stop_loss_pct=10.0, max_allocation_usd=500.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("-60"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is True

    @pytest.mark.asyncio
    async def test_pnl_from_exchange_positive_does_not_trigger(self) -> None:
        """DoD: open $1000 BTC, close with PnL +$100 → function returns False (no stop-loss)."""
        sub = _active_sub(stop_loss_pct=50.0, max_allocation_usd=2000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("100"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is False

    @pytest.mark.asyncio
    async def test_pnl_uses_realized_pnl_field_not_notional(self) -> None:
        """DoD: stop-loss checks realized_pnl from DB, not price*size arithmetic."""
        # A trade that in old code (price=250, size=1) would give pnl=-250 → triggers
        # But actual realized_pnl from HL = +$100 (profitable close) → should NOT trigger
        sub = _active_sub(stop_loss_pct=20.0, max_allocation_usd=1000.0)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=sub)),
                MagicMock(scalar_one=MagicMock(return_value=Decimal("100"))),
            ]
        )
        result = await check_subscription_stop_loss(db, subscription_id=1)
        assert result is False


def _margin_summary(account_value: float, margin_used: float) -> MarginSummary:
    """Build a MarginSummary for tests without going through field aliases."""
    return MarginSummary.model_construct(
        account_value=Decimal(str(account_value)),
        total_margin_used=Decimal(str(margin_used)),
        total_raw_usd=Decimal(str(account_value)),
    )


class TestCheckPortfolioRisk:
    @pytest.mark.asyncio
    async def test_blocks_when_margin_summary_is_none(self) -> None:
        """No HL account → subscription not allowed."""
        db = AsyncMock()
        allowed, reason = await check_portfolio_risk(
            db, user_id=1, new_allocation=100.0, max_leverage=2.0, margin_summary=None
        )
        assert allowed is False
        assert "HL account" in reason

    @pytest.mark.asyncio
    async def test_allows_when_free_margin_is_sufficient(self) -> None:
        # equity=1000, used=0 → available=1000
        # estimated_margin = 100 / 2 = 50
        # cap = 1000 * 0.8 = 800 → 50 <= 800 → allow
        db = AsyncMock()
        summary = _margin_summary(account_value=1000.0, margin_used=0.0)
        allowed, reason = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=100.0,
            max_leverage=2.0,
            margin_summary=summary,
        )
        assert allowed is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_blocks_when_estimated_margin_exceeds_cap(self) -> None:
        # equity=1000, used=900 → available=100
        # estimated_margin = 500 / 1 = 500
        # cap = 100 * 0.8 = 80 → 500 > 80 → block
        db = AsyncMock()
        summary = _margin_summary(account_value=1000.0, margin_used=900.0)
        allowed, reason = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=500.0,
            max_leverage=1.0,
            margin_summary=summary,
        )
        assert allowed is False
        assert "margin" in reason.lower()

    @pytest.mark.asyncio
    async def test_leverage_scales_estimated_margin(self) -> None:
        # equity=1000, used=0 → available=1000
        # With leverage=10: estimated = 1000 / 10 = 100 → cap=800 → allow
        # With leverage=1:  estimated = 1000 / 1 = 1000 → cap=800 → block
        db = AsyncMock()
        summary = _margin_summary(account_value=1000.0, margin_used=0.0)

        allowed_high_lev, _ = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=1000.0,
            max_leverage=10.0,
            margin_summary=summary,
        )
        allowed_low_lev, _ = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=1000.0,
            max_leverage=1.0,
            margin_summary=summary,
        )

        assert allowed_high_lev is True
        assert allowed_low_lev is False

    @pytest.mark.asyncio
    async def test_allows_at_exact_cap_boundary(self) -> None:
        # available=1000, estimated_margin exactly = cap (1000*0.8=800) → allow
        db = AsyncMock()
        summary = _margin_summary(account_value=1000.0, margin_used=0.0)
        # 800 / 1.0 = 800 == cap → allow
        allowed, reason = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=800.0,
            max_leverage=1.0,
            margin_summary=summary,
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_blocks_one_cent_above_cap(self) -> None:
        # available=1000, cap=800; estimated=800.01 → block
        db = AsyncMock()
        summary = _margin_summary(account_value=1000.0, margin_used=0.0)
        allowed, _ = await check_portfolio_risk(
            db,
            user_id=1,
            new_allocation=800.01,
            max_leverage=1.0,
            margin_summary=summary,
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_max_allocation_fraction_constant(self) -> None:
        assert Decimal("0.8") == MAX_ALLOCATION_EQUITY_FRACTION


def _build_margin_summary(account_value: float) -> MarginSummary:
    return MarginSummary.model_construct(
        account_value=Decimal(str(account_value)),
        total_margin_used=Decimal("0"),
        total_raw_usd=Decimal(str(account_value)),
    )


class TestCheckPortfolioStopLoss:
    """Tests for the account-level portfolio stop-loss check."""

    def _mock_hl(self, account_value: float) -> MagicMock:
        hl = MagicMock()
        hl.get_account_summary = AsyncMock(
            return_value=_build_margin_summary(account_value)
        )
        return hl

    def _mock_redis(self, baseline_str: str | None) -> MagicMock:
        r = MagicMock()
        r.get = MagicMock(return_value=baseline_str)
        r.setex = MagicMock()
        return r

    @pytest.mark.asyncio
    async def test_sets_baseline_and_returns_false_on_first_call(self) -> None:
        """First call within the day: baseline is set, check returns False."""
        r = self._mock_redis(None)
        hl = self._mock_hl(1000.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=1, user_hl_address="0xABCD", portfolio_stop_loss_pct=20.0
            )

        assert result is False
        r.setex.assert_called_once()
        # baseline stored = current equity
        call_args = r.setex.call_args
        assert call_args[0][0] == "hl:equity_baseline:1"
        assert float(call_args[0][2]) == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_returns_false_when_loss_below_threshold(self) -> None:
        """Loss of 10% with 20% threshold → no trigger."""
        # baseline=1000, current=900 → loss=10%
        r = self._mock_redis("1000.0")
        hl = self._mock_hl(900.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=1, user_hl_address="0xABCD", portfolio_stop_loss_pct=20.0
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_loss_exceeds_threshold(self) -> None:
        """DoD: portfolio lost 25% with 20% threshold → trigger."""
        # baseline=1000, current=750 → loss=25%
        r = self._mock_redis("1000.0")
        hl = self._mock_hl(750.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=1, user_hl_address="0xABCD", portfolio_stop_loss_pct=20.0
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_at_exact_threshold(self) -> None:
        """Loss exactly equals threshold (20%) → trigger."""
        # baseline=1000, current=800 → loss=20%
        r = self._mock_redis("1000.0")
        hl = self._mock_hl(800.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=1, user_hl_address="0xABCD", portfolio_stop_loss_pct=20.0
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_equity_grew(self) -> None:
        """Account grew: no trigger even with low threshold."""
        # baseline=1000, current=1200 → gain=20%
        r = self._mock_redis("1000.0")
        hl = self._mock_hl(1200.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=2, user_hl_address="0xDEAD", portfolio_stop_loss_pct=5.0
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_baseline_is_zero(self) -> None:
        """Baseline of zero is invalid → skip check safely."""
        r = self._mock_redis("0.0")
        hl = self._mock_hl(500.0)

        with (
            patch("app.services.risk_manager.HyperliquidInfoClient", return_value=hl),
            patch("app.services.risk_manager.get_redis_client", return_value=r),
        ):
            result = await check_portfolio_stop_loss(
                user_id=3, user_hl_address="0xBEEF", portfolio_stop_loss_pct=10.0
            )

        assert result is False

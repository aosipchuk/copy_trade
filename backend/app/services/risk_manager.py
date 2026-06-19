from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.services.copy_engine.constants import MAX_ALLOCATION_EQUITY_FRACTION
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary

_EQUITY_BASELINE_TTL: int = 86400  # 24 hours

logger = get_logger(__name__)


async def check_subscription_stop_loss(db: AsyncSession, subscription_id: int) -> bool:
    """
    Return True if the subscription has hit its stop-loss threshold.
    Uses realized PnL from filled close UserTrade rows (sourced from HL fills).
    """
    result = await db.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None or not sub.is_active:
        return False

    realized_pnl = await _get_realized_pnl(db, subscription_id)
    stop_loss_amount = (
        Decimal(str(sub.stop_loss_pct)) / 100 * Decimal(str(sub.max_allocation_usd))
    )

    if realized_pnl < Decimal("0") and abs(realized_pnl) >= stop_loss_amount:
        logger.warning(
            "stop_loss_triggered",
            subscription_id=subscription_id,
            realized_pnl=float(realized_pnl),
            threshold=float(stop_loss_amount),
        )
        return True
    return False


async def _get_realized_pnl(db: AsyncSession, subscription_id: int) -> Decimal:
    """
    Return sum of realized_pnl from filled close trades for the subscription.
    Values come directly from Hyperliquid fills (closedPnl field).
    Positive = profit, negative = loss.
    """
    result = await db.execute(
        select(func.coalesce(func.sum(UserTrade.realized_pnl), 0)).where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.trade_type == "close",
            UserTrade.status == "filled",
            UserTrade.realized_pnl.is_not(None),
        )
    )
    return Decimal(str(result.scalar_one()))


async def check_portfolio_stop_loss(
    user_id: int,
    user_hl_address: str,
    portfolio_stop_loss_pct: float,
) -> bool:
    """
    Return True if the user's portfolio has hit the account-level stop-loss.

    Compares current HL equity against a 24-hour baseline stored in Redis
    (key: hl:equity_baseline:{user_id}).  On the first call of the day the
    baseline is set and False is returned — the loss check only activates
    from the second call onward.

    The HL margin summary is already cached by HyperliquidInfoClient (30 s),
    so this adds no extra API load when check_stop_losses runs every minute.
    """
    hl = HyperliquidInfoClient()
    summary = await hl.get_account_summary(user_hl_address)
    current_equity = summary.account_value

    r = get_redis_client()
    cache_key = f"hl:equity_baseline:{user_id}"
    baseline_str: str | None = r.get(cache_key)

    if baseline_str is None:
        r.setex(cache_key, _EQUITY_BASELINE_TTL, str(float(current_equity)))
        logger.debug(
            "portfolio_baseline_set", user_id=user_id, equity=float(current_equity)
        )
        return False

    baseline = Decimal(baseline_str)
    if baseline <= 0:
        return False

    loss_ratio = (current_equity - baseline) / baseline
    threshold = Decimal(str(portfolio_stop_loss_pct)) / 100

    if loss_ratio <= -threshold:
        logger.warning(
            "portfolio_stop_loss_triggered",
            user_id=user_id,
            current_equity=float(current_equity),
            baseline=float(baseline),
            loss_pct=float(loss_ratio * 100),
            threshold_pct=portfolio_stop_loss_pct,
        )
        return True

    return False


async def check_portfolio_risk(
    db: AsyncSession,
    user_id: int,
    new_allocation: float,
    max_leverage: float,
    margin_summary: MarginSummary | None,
) -> tuple[bool, str]:
    """
    Check portfolio-level risk before creating a subscription.
    Uses real margin from HL: estimated_margin = new_allocation / max_leverage.
    Returns (allowed, reason). reason is empty string when allowed.
    """
    if margin_summary is None:
        return False, "HL account required for subscription"

    available = margin_summary.account_value - margin_summary.total_margin_used
    estimated_margin = Decimal(str(new_allocation)) / Decimal(str(max_leverage))
    margin_cap = available * MAX_ALLOCATION_EQUITY_FRACTION

    if estimated_margin > margin_cap:
        pct = int(MAX_ALLOCATION_EQUITY_FRACTION * 100)
        return (
            False,
            f"Insufficient free margin — needs ~{float(estimated_margin):.0f} USD,"
            f" only {float(margin_cap):.0f} USD ({pct}% of free margin) available",
        )

    return True, ""

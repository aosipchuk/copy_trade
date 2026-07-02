from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_fixed

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.user import User
from app.services.copy_engine.constants import PENDING_TRADE_TIMEOUT_SECONDS
from app.services.copy_engine.exceptions import NonRetryableError

logger = get_logger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_not_exception_type(NonRetryableError),
    reraise=True,
)
async def execute_copy_trade_async(signal_id: int, subscription_id: int) -> None:
    """Execute a copy trade for a specific subscription based on a signal."""
    from app.services.copy_engine.executor import execute_copy_trade as _exec

    try:
        await _exec(signal_id, subscription_id)
    except NonRetryableError as exc:
        logger.warning(
            "copy_trade_non_retryable",
            signal_id=signal_id,
            subscription_id=subscription_id,
            reason=str(exc),
        )
        raise


async def simulate_demo_trade_async(signal_id: int, subscription_id: int) -> None:
    """Paper-trade simulation for a demo subscription."""
    try:
        from app.services.copy_engine.demo_executor import (
            simulate_demo_trade as _sim,
        )

        await _sim(signal_id, subscription_id)
    except Exception as exc:
        logger.error(
            "simulate_demo_trade_failed",
            signal_id=signal_id,
            subscription_id=subscription_id,
            error=str(exc),
        )


async def check_stop_losses_async() -> None:
    """Check all active subscriptions and deactivate those that hit stop-loss."""
    from app.services.notifications.telegram import (
        format_portfolio_stop_loss_hit,
        format_stop_loss_hit,
        send_trade_notification,
    )
    from app.services.risk_manager import (
        check_portfolio_stop_loss,
        check_subscription_stop_loss,
    )

    async with get_db_session() as db:
        result = await db.execute(
            select(Subscription.id, Subscription.user_id).where(
                Subscription.is_active == True  # noqa: E712
            )
        )
        rows = result.all()

    # --- Per-subscription stop-loss ---
    for sub_id, user_id in rows:
        async with get_db_session() as db:
            try:
                hit = await check_subscription_stop_loss(db, sub_id)
                if not hit:
                    continue

                sub_res = await db.execute(
                    select(Subscription).where(Subscription.id == sub_id)
                )
                sub = sub_res.scalar_one_or_none()
                if sub is None:
                    continue
                sub.is_active = False

                user_res = await db.execute(select(User).where(User.id == user_id))
                user = user_res.scalar_one_or_none()
                if user:
                    from app.models.trader import Trader

                    trader_q = await db.execute(
                        select(Trader.display_name, Trader.hl_address).where(
                            Trader.id == sub.trader_id
                        )
                    )
                    trader_row = trader_q.one_or_none()
                    name = trader_row[0] if trader_row else None
                    addr = trader_row[1] if trader_row else "unknown"
                    await send_trade_notification(
                        user.telegram_id, format_stop_loss_hit(name, addr)
                    )

                    from app.services.copy_engine.executor import (
                        close_positions_for_subscription,
                    )

                    await close_positions_for_subscription(user_id, sub_id)

                logger.info("stop_loss_deactivated", subscription_id=sub_id)
            except Exception as exc:
                logger.error(
                    "stop_loss_check_error", subscription_id=sub_id, error=str(exc)
                )

    # --- Portfolio-level stop-loss (per unique user) ---
    unique_user_ids = {uid for _, uid in rows}
    for user_id in unique_user_ids:
        async with get_db_session() as db:
            try:
                user_res = await db.execute(select(User).where(User.id == user_id))
                user = user_res.scalar_one_or_none()
                if (
                    user is None
                    or not user.hl_address
                    or user.portfolio_stop_loss_pct is None
                ):
                    continue

                hit = await check_portfolio_stop_loss(
                    user_id=user_id,
                    user_hl_address=user.hl_address,
                    portfolio_stop_loss_pct=float(user.portfolio_stop_loss_pct),
                )
                if not hit:
                    continue

                # Deactivate ALL remaining active subscriptions for this user
                subs_res = await db.execute(
                    select(Subscription).where(
                        Subscription.user_id == user_id,
                        Subscription.is_active == True,  # noqa: E712
                    )
                )
                active_subs = subs_res.scalars().all()
                for sub in active_subs:
                    sub.is_active = False

                logger.info(
                    "portfolio_stop_loss_deactivated",
                    user_id=user_id,
                    subs_count=len(active_subs),
                )

                # Calculate loss for notification
                from app.services.hyperliquid.info_client import HyperliquidInfoClient

                hl = HyperliquidInfoClient()
                summary = await hl.get_account_summary(user.hl_address)
                from decimal import Decimal as BaselineDecimal

                from app.core.redis_client import get_redis_client

                r = get_redis_client()
                baseline_str = r.get(f"hl:equity_baseline:{user_id}")
                _bl = baseline_str
                baseline = BaselineDecimal(_bl) if _bl else summary.account_value
                loss_pct = (
                    float((summary.account_value - baseline) / baseline * 100)
                    if baseline > 0
                    else 0.0
                )

                await send_trade_notification(
                    user.telegram_id,
                    format_portfolio_stop_loss_hit(
                        loss_pct, float(user.portfolio_stop_loss_pct)
                    ),
                )

                # Close all positions
                from app.services.copy_engine.executor import (
                    close_all_positions_for_user,
                )

                await close_all_positions_for_user(user_id)

            except Exception as exc:
                logger.error(
                    "portfolio_stop_loss_check_error", user_id=user_id, error=str(exc)
                )


async def close_all_positions_for_user_async(user_id: int) -> None:
    """Close all open HL positions for a user during emergency stop."""
    from app.services.copy_engine.executor import close_all_positions_for_user as _exec

    count = await _exec(user_id)
    logger.info("emergency_stop_complete", user_id=user_id, closed=count)


async def close_subscription_positions_async(
    user_id: int, subscription_id: int
) -> None:
    """Close all open HL positions for a single subscription on deletion."""
    from app.services.copy_engine.executor import (
        close_positions_for_subscription as _exec,
    )

    await _exec(user_id, subscription_id)
    logger.info(
        "subscription_positions_closed",
        user_id=user_id,
        subscription_id=subscription_id,
    )


async def monitor_pending_trades_async() -> None:
    """Update status of pending trades by checking Hyperliquid order status."""
    from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient
    from app.services.hyperliquid.info_client import HyperliquidInfoClient

    timeout_cutoff = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(
        seconds=PENDING_TRADE_TIMEOUT_SECONDS
    )

    async with get_db_session() as db:
        result = await db.execute(
            select(UserTrade)
            .join(Subscription, UserTrade.subscription_id == Subscription.id)
            .where(UserTrade.status == "pending")
        )
        trades = result.scalars().all()

    exchange = HyperliquidExchangeClient()
    hl_info = HyperliquidInfoClient()

    for trade in trades:
        async with get_db_session() as db:
            try:
                # Reload trade in this session
                tr_res = await db.execute(
                    select(UserTrade).where(UserTrade.id == trade.id)
                )
                tr = tr_res.scalar_one_or_none()
                if tr is None or tr.status != "pending":
                    continue

                # Timeout check
                if tr.executed_at < timeout_cutoff:
                    tr.status = "failed"
                    tr.error_msg = "Timed out waiting for fill"
                    logger.info("trade_timed_out", trade_id=tr.id)
                    continue

                if tr.hl_order_id is None:
                    tr.status = "failed"
                    tr.error_msg = "No order ID recorded"
                    continue

                # Get owner address to query order status
                sub_res = await db.execute(
                    select(Subscription).where(Subscription.id == tr.subscription_id)
                )
                sub = sub_res.scalar_one_or_none()
                if sub is None:
                    continue

                user_res = await db.execute(select(User).where(User.id == sub.user_id))
                user = user_res.scalar_one_or_none()
                if user is None or not user.hl_address:
                    continue

                status = await exchange.get_order_status(
                    user.hl_address, tr.hl_order_id
                )
                if status == "filled":
                    tr.status = "filled"
                    logger.info("trade_filled", trade_id=tr.id, order_id=tr.hl_order_id)
                    # For close trades, fetch the actual realized PnL from HL fills
                    if tr.trade_type == "close":
                        fills = await hl_info.get_fills(user.hl_address, limit=None)
                        matching = next(
                            (f for f in fills if f.oid == tr.hl_order_id), None
                        )
                        if matching is not None:
                            tr.realized_pnl = float(matching.closed_pnl)
                            logger.info(
                                "trade_pnl_recorded",
                                trade_id=tr.id,
                                pnl=float(matching.closed_pnl),
                            )
                elif status == "cancelled":
                    tr.status = "cancelled"
                    logger.info("trade_cancelled", trade_id=tr.id)

            except Exception as exc:
                logger.error("monitor_trade_error", trade_id=trade.id, error=str(exc))

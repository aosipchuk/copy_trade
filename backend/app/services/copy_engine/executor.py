import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.user import User, UserAgent
from app.services.copy_engine.constants import (
    COIN_WHITELIST,
    DEDUP_TTL_SECONDS,
    MIN_TRADE_USD,
)
from app.services.copy_engine.exceptions import NonRetryableError
from app.services.copy_engine.order_builder import build_close_order, signal_to_order
from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.notifications.telegram import (
    format_trade_failed,
    format_trade_filled,
    send_trade_notification,
)
from app.services.portfolio.subscription_lifecycle import (
    subscription_execution_allowed_clause,
)
from app.services.risk_manager import check_subscription_stop_loss
from app.services.wallet.agent_manager import decrypt_agent_key

logger = get_logger(__name__)


def _dedup_key(signal_id: int, subscription_id: int) -> str:
    return f"copy:dedup:{signal_id}:sub:{subscription_id}"


def _is_dedup(signal_id: int, subscription_id: int) -> bool:
    r = get_redis_client()
    key = _dedup_key(signal_id, subscription_id)
    if r.exists(key):
        return True
    r.setex(key, DEDUP_TTL_SECONDS, "1")
    return False


# One failure notification per (user, coin) per 5 min
_NOTIFY_FAIL_TTL_SECONDS: int = 300


def _should_notify_failure(user_id: int, coin: str) -> bool:
    r = get_redis_client()
    key = f"notif:fail:{user_id}:{coin}"
    if r.exists(key):
        return False
    r.setex(key, _NOTIFY_FAIL_TTL_SECONDS, "1")
    return True


async def _resolve_asset_index(coin: str) -> int | None:
    client = HyperliquidInfoClient()
    meta = await client.get_meta()
    return meta.asset_index(coin)


async def execute_copy_trade(signal_id: int, subscription_id: int) -> None:
    """
    Full copy-trade execution pipeline for one signal + one subscription.
    Pre-checks → build order → place on HL → record UserTrade.
    """
    if _is_dedup(signal_id, subscription_id):
        logger.info(
            "copy_trade_dedup_skip",
            signal_id=signal_id,
            subscription_id=subscription_id,
        )
        return

    async with get_db_session() as db:
        signal, subscription, agent, user = await _load_context(
            db, signal_id, subscription_id
        )
        if signal is None or subscription is None or agent is None or user is None:
            return

        user_id = user.id
        telegram_id = user.telegram_id
        coin = signal.coin

        # Pre-check: builder fee approved (required when builder is configured)
        if settings.builder_address and user.builder_fee_approved_at is None:
            logger.info(
                "copy_trade_blocked_builder_fee_not_approved",
                user_id=user_id,
                signal_id=signal_id,
            )
            return

        # Pre-check: coin whitelisted
        if coin not in COIN_WHITELIST:
            logger.info("copy_trade_coin_skip", coin=coin)
            return

        # Pre-check: stop-loss not yet hit
        stop_loss_hit = await check_subscription_stop_loss(db, subscription.id)
        if stop_loss_hit:
            logger.info("copy_trade_stop_loss_skip", subscription_id=subscription.id)
            return

        # Get agent key
        agent_key = decrypt_agent_key(bytes(agent.agent_key_enc))
        user_address = user.hl_address
        if not user_address:
            await _fail_trade(
                db, signal, subscription, "User has no HL address configured"
            )
            if _should_notify_failure(user_id, coin):
                await send_trade_notification(
                    telegram_id, format_trade_failed(coin, "no HL address configured")
                )
            await db.commit()
            raise NonRetryableError("no HL address configured")

        # Fetch market data
        hl = HyperliquidInfoClient()
        mids, meta = await asyncio.gather(hl.get_all_mids(), hl.get_meta())

        mid_str = mids.get(coin)
        if mid_str is None:
            await _fail_trade(db, signal, subscription, f"No mid price for {coin}")
            return

        mid_price = Decimal(mid_str)

        # Balance check
        summary = await hl.get_account_summary(user_address)
        user_equity = summary.account_value
        if user_equity < MIN_TRADE_USD:
            await _fail_trade(db, signal, subscription, "Insufficient HL balance")
            if _should_notify_failure(user_id, coin):
                await send_trade_notification(
                    telegram_id,
                    format_trade_failed(coin, "insufficient balance"),
                )
            await db.commit()
            raise NonRetryableError("insufficient balance")

        asset_meta_obj = next((a for a in meta.universe if a.name == coin), None)
        if asset_meta_obj is None:
            await _fail_trade(db, signal, subscription, f"Asset {coin} not in HL meta")
            return

        asset_idx = meta.asset_index(coin)
        if asset_idx is None:
            await _fail_trade(
                db, signal, subscription, f"Asset index not found for {coin}"
            )  # noqa: E501
            return

        exchange = HyperliquidExchangeClient()
        include_builder = (
            bool(settings.builder_address) and user.builder_fee_approved_at is not None
        )

        # CLOSE signal: close the user's matching position
        if signal.signal_type == "CLOSE":
            await _handle_close(
                db,
                exchange,
                agent_key,
                user_address,
                signal,
                subscription,
                coin,
                asset_idx,
                mid_price,
                telegram_id,
                include_builder=include_builder,
            )
            return

        # OPEN / UPDATE signal: place a new order
        order = signal_to_order(
            signal=signal,
            subscription=subscription,
            user_equity=user_equity,
            mid_price=mid_price,
            asset_meta=asset_meta_obj,
        )
        if order is None:
            logger.debug("copy_trade_order_skipped", signal_id=signal_id)
            return

        # Patch asset_index (signal_to_order uses placeholder 0)
        from dataclasses import replace as dc_replace

        order = dc_replace(order, asset_index=asset_idx)

        # Margin check — skip if placing would leave < 10% equity as free margin
        margin_buffer = Decimal("0.1")
        notional = order.size * order.limit_px
        available_margin = summary.account_value - summary.total_margin_used
        order_margin_estimate = notional / Decimal(str(subscription.max_leverage))
        min_free = summary.account_value * margin_buffer
        if available_margin - order_margin_estimate < min_free:
            await _fail_trade(db, signal, subscription, "Insufficient free margin")
            logger.warning(
                "copy_trade_insufficient_margin",
                signal_id=signal_id,
                available=float(available_margin),
                needed=float(order_margin_estimate),
            )
            return

        order_id = await exchange.place_order(
            agent_key=agent_key,
            coin=coin,
            asset_index=order.asset_index,
            is_buy=order.is_buy,
            size=order.size,
            limit_px=order.limit_px,
            include_builder=include_builder,
        )

        side = "long" if order.is_buy else "short"
        if order_id is not None:
            trade = UserTrade(
                subscription_id=subscription.id,
                signal_id=signal.id,
                hl_order_id=order_id,
                coin=coin,
                side=side,
                size=float(order.size),
                price=float(order.limit_px),
                status="pending",
                trade_type="open",
            )
            db.add(trade)
            logger.info(
                "copy_trade_placed",
                coin=coin,
                order_id=order_id,
                user_id=user_id,
            )
            await send_trade_notification(
                telegram_id,
                format_trade_filled(
                    coin, side, float(order.size), float(order.limit_px)
                ),  # noqa: E501
            )
        else:
            trade = UserTrade(
                subscription_id=subscription.id,
                signal_id=signal.id,
                coin=coin,
                side=side,
                size=float(order.size),
                price=float(order.limit_px),
                status="failed",
                trade_type="open",
                error_msg="Order rejected by Hyperliquid",
            )
            db.add(trade)
            if _should_notify_failure(user_id, coin):
                await send_trade_notification(
                    telegram_id,
                    format_trade_failed(coin, "order rejected by exchange"),
                )


async def _load_context(
    db: AsyncSession, signal_id: int, subscription_id: int
) -> tuple[Signal | None, Subscription | None, UserAgent | None, User | None]:
    signal_res = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = signal_res.scalar_one_or_none()
    if signal is None:
        logger.warning("executor_signal_not_found", signal_id=signal_id)
        return None, None, None, None

    sub_res = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.trader_id == signal.trader_id,
            Subscription.is_active == True,  # noqa: E712
            Subscription.is_demo == False,  # noqa: E712
            subscription_execution_allowed_clause(),
        )
    )
    subscription = sub_res.scalar_one_or_none()
    if subscription is None:
        logger.debug(
            "executor_no_active_subscription",
            subscription_id=subscription_id,
            signal_id=signal_id,
        )  # noqa: E501
        return None, None, None, None

    agent_res = await db.execute(
        select(UserAgent).where(
            UserAgent.user_id == subscription.user_id,
            UserAgent.is_active == True,  # noqa: E712
            UserAgent.approved_at.is_not(None),
        )
    )
    agent = agent_res.scalar_one_or_none()
    if agent is None:
        logger.info("executor_no_active_agent", user_id=subscription.user_id)
        return None, None, None, None

    user_res = await db.execute(select(User).where(User.id == subscription.user_id))
    user = user_res.scalar_one_or_none()
    return signal, subscription, agent, user


async def _fail_trade(
    db: AsyncSession,
    signal: Signal,
    subscription: Subscription,
    reason: str,
) -> None:
    trade_type = "close" if signal.signal_type == "CLOSE" else "open"
    trade = UserTrade(
        subscription_id=subscription.id,
        signal_id=signal.id,
        coin=signal.coin,
        side=signal.side,
        status="failed",
        trade_type=trade_type,
        error_msg=reason,
    )
    db.add(trade)
    logger.warning("copy_trade_failed", reason=reason, signal_id=signal.id)


async def _handle_close(
    db: AsyncSession,
    exchange: HyperliquidExchangeClient,
    agent_key: str,
    user_address: str,
    signal: Signal,
    subscription: Subscription,
    coin: str,
    asset_idx: int,
    mid_price: Decimal,
    telegram_id: int,
    include_builder: bool = False,
) -> None:
    """Close the user's copy position for the given coin/side."""
    hl = HyperliquidInfoClient()
    positions = await hl.get_positions(user_address)
    matching = [p for p in positions if p.coin == coin]
    if not matching:
        logger.debug("close_signal_no_position", coin=coin, user_id=telegram_id)
        return

    pos = matching[0]
    is_long = pos.szi > Decimal("0")
    order = build_close_order(
        coin=coin,
        asset_index=asset_idx,
        is_long=is_long,
        size=pos.abs_size,
        mid_price=mid_price,
    )
    order_id = await exchange.close_position(
        agent_key=agent_key,
        coin=coin,
        asset_index=order.asset_index,
        is_long=is_long,
        size=order.size,
        limit_px=order.limit_px,
        include_builder=include_builder,
    )
    side = "long" if is_long else "short"
    trade = UserTrade(
        subscription_id=subscription.id,
        signal_id=signal.id,
        hl_order_id=order_id,
        coin=coin,
        side=side,
        size=float(order.size),
        price=float(order.limit_px),
        status="pending" if order_id else "failed",
        trade_type="close",
        error_msg=None if order_id else "Close order rejected",
    )
    db.add(trade)
    if order_id:
        logger.info("copy_close_placed", coin=coin, order_id=order_id)
    else:
        logger.warning("copy_close_failed", coin=coin)


async def close_all_positions_for_user(user_id: int) -> int:
    """Close ALL open HL positions for a user (emergency stop). Returns count closed."""
    async with get_db_session() as db:
        agent_res = await db.execute(
            select(UserAgent).where(
                UserAgent.user_id == user_id,
                UserAgent.is_active == True,  # noqa: E712
                UserAgent.approved_at.is_not(None),
            )
        )
        agent = agent_res.scalar_one_or_none()
        user_res = await db.execute(select(User).where(User.id == user_id))
        user = user_res.scalar_one_or_none()

        if agent is None or user is None or not user.hl_address:
            return 0

        agent_key = decrypt_agent_key(bytes(agent.agent_key_enc))
        include_builder = (
            bool(settings.builder_address) and user.builder_fee_approved_at is not None
        )
        hl = HyperliquidInfoClient()
        exchange = HyperliquidExchangeClient()

        positions, mids, meta = await asyncio.gather(
            hl.get_positions(user.hl_address),
            hl.get_all_mids(),
            hl.get_meta(),
        )

        closed = 0
        for pos in positions:
            mid_str = mids.get(pos.coin)
            if not mid_str:
                continue
            asset_idx = meta.asset_index(pos.coin)
            if asset_idx is None:
                continue

            mid_price = Decimal(mid_str)
            is_long = pos.szi > Decimal("0")
            order = build_close_order(
                coin=pos.coin,
                asset_index=asset_idx,
                is_long=is_long,
                size=pos.abs_size,
                mid_price=mid_price,
            )
            order_id = await exchange.close_position(
                agent_key=agent_key,
                coin=pos.coin,
                asset_index=order.asset_index,
                is_long=is_long,
                size=order.size,
                limit_px=order.limit_px,
                include_builder=include_builder,
            )
            if order_id:
                closed += 1
                logger.info(
                    "emergency_close_placed",
                    coin=pos.coin,
                    order_id=order_id,
                    user_id=user_id,
                )
            else:
                logger.warning("emergency_close_failed", coin=pos.coin, user_id=user_id)

        return closed


async def close_positions_for_subscription(user_id: int, subscription_id: int) -> None:
    """
    Attempt to close all HL positions associated with the given subscription.
    Called on subscription delete.
    """
    async with get_db_session() as db:
        sub_res = await db.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        subscription = sub_res.scalar_one_or_none()
        if subscription is None:
            return

        agent_res = await db.execute(
            select(UserAgent).where(
                UserAgent.user_id == user_id,
                UserAgent.is_active == True,  # noqa: E712
                UserAgent.approved_at.is_not(None),
            )
        )
        agent = agent_res.scalar_one_or_none()
        user_res = await db.execute(select(User).where(User.id == user_id))
        user = user_res.scalar_one_or_none()

        if agent is None or user is None or not user.hl_address:
            return

        agent_key = decrypt_agent_key(bytes(agent.agent_key_enc))
        include_builder = (
            bool(settings.builder_address) and user.builder_fee_approved_at is not None
        )
        hl = HyperliquidInfoClient()
        exchange = HyperliquidExchangeClient()

        positions, mids, meta = await asyncio.gather(
            hl.get_positions(user.hl_address),
            hl.get_all_mids(),
            hl.get_meta(),
        )

        for pos in positions:
            if pos.coin not in COIN_WHITELIST:
                continue
            mid_str = mids.get(pos.coin)
            if not mid_str:
                continue
            asset_idx = meta.asset_index(pos.coin)
            if asset_idx is None:
                continue

            mid_price = Decimal(mid_str)
            is_long = pos.szi > Decimal("0")
            order = build_close_order(
                coin=pos.coin,
                asset_index=asset_idx,
                is_long=is_long,
                size=pos.abs_size,
                mid_price=mid_price,
            )
            await exchange.close_position(
                agent_key=agent_key,
                coin=pos.coin,
                asset_index=order.asset_index,
                is_long=is_long,
                size=order.size,
                limit_px=order.limit_px,
                include_builder=include_builder,
            )
            logger.info(
                "subscription_position_closed",
                subscription_id=subscription_id,
                coin=pos.coin,
            )

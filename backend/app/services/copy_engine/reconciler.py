from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyAccountPosition,
    CopyExecutionOrder,
    CopyPositionTarget,
)
from app.models.subscription import Subscription
from app.services.copy_engine.account_risk import (
    RiskAllocation,
    required_initial_margin,
    risk_increase_allowed,
)
from app.models.user import User, UserAgent
from app.services.copy_engine.account_state import AccountStateReader
from app.services.copy_engine.allocation_service import apply_filled_delta
from app.services.copy_engine.execution_state import block_account
from app.services.copy_engine.intent_service import (
    ACTIVE_INTENT_STATUSES,
    OpposingTargetsError,
    prepare_market_intent,
)
from app.services.copy_engine.locking import lock_market
from app.services.copy_engine.market_registry import MarketRegistry
from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.wallet.agent_manager import decrypt_agent_key

logger = get_logger(__name__)


def _decimal(value: object | None) -> Decimal:
    return Decimal(str(value or 0))


async def _active_intent_id(user_id: int, dex: str, coin: str) -> int | None:
    async with get_db_session() as db:
        result = await db.execute(
            select(CopyExecutionOrder.id).where(
                CopyExecutionOrder.user_id == user_id,
                CopyExecutionOrder.dex == dex,
                CopyExecutionOrder.coin == coin,
                CopyExecutionOrder.kind.in_(("exchange", "emergency")),
                CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES),
            )
        )
        return result.scalar_one_or_none()


async def _apply_exchange_fill(
    order_id: int,
    *,
    exchange_oid: int | None,
    filled_size: Decimal,
    average_price: Decimal,
    terminal: bool,
) -> None:
    async with get_db_session() as db:
        order = await db.get(CopyExecutionOrder, order_id, with_for_update=True)
        if order is None:
            return
        incremental = filled_size - _decimal(order.filled_size)
        if incremental < 0:
            await block_account(
                db,
                order.user_id,
                "exchange_fill_regressed",
                {"intent_id": order.id},
            )
            return
        order.exchange_oid = exchange_oid or order.exchange_oid
        if incremental:
            signed = incremental if order.is_buy else -incremental
            await apply_filled_delta(db, order, signed, average_price)
            position_result = await db.execute(
                select(CopyAccountPosition)
                .where(
                    CopyAccountPosition.user_id == order.user_id,
                    CopyAccountPosition.dex == order.dex,
                    CopyAccountPosition.coin == order.coin,
                )
                .with_for_update()
            )
            position = position_result.scalar_one()
            position.confirmed_actual_size = (
                _decimal(position.confirmed_actual_size) + signed
            )
            position.pending_explained_delta = (
                _decimal(position.pending_explained_delta) - signed
            )
            position.status = "dirty"
        order.filled_size = filled_size
        order.average_price = average_price
        order.status = "filled" if terminal else "partial"
        if terminal:
            order.completed_at = datetime.now(tz=UTC).replace(tzinfo=None)


async def execute_intent(order_id: int) -> None:
    async with get_db_session() as db:
        order = await db.get(CopyExecutionOrder, order_id)
        if order is None or order.status != "pending":
            return
        if (
            order.kind != "emergency"
            and not settings.copy_engine_v2_live_enabled
        ):
            return
        state = await db.get(CopyAccountExecutionState, order.user_id)
        user = await db.get(User, order.user_id)
        agent_result = await db.execute(
            select(UserAgent).where(
                UserAgent.user_id == order.user_id,
                UserAgent.is_active.is_(True),
                UserAgent.approved_at.is_not(None),
            )
        )
        agent = agent_result.scalar_one_or_none()
        if (
            state is None
            or (
                state.status != "active"
                and order.kind != "emergency"
            )
            or state.account_address is None
            or user is None
            or agent is None
        ):
            return
        agent_key = decrypt_agent_key(bytes(agent.agent_key_enc))
        vault_address = state.vault_address
        include_builder = bool(
            settings.builder_address and user.builder_fee_approved_at is not None
        )
        market = await MarketRegistry().require_market(order.dex, order.coin)
        cloid = order.cloid
        requested_size = _decimal(order.rounded_size)
        limit_price = _decimal(order.limit_price)
        is_buy = order.is_buy
        reduce_only = order.reduce_only

    exchange = HyperliquidExchangeClient()
    try:
        result = await exchange.place_order(
            agent_key=agent_key,
            coin=order.coin,
            asset_index=market.asset_id,
            is_buy=is_buy,
            size=requested_size,
            limit_px=limit_price,
            cloid=cloid,
            reduce_only=reduce_only,
            include_builder=include_builder,
            vault_address=vault_address,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        async with get_db_session() as db:
            current = await db.get(CopyExecutionOrder, order_id, with_for_update=True)
            if current is not None and current.status == "pending":
                current.status = "unknown"
                current.error_code = "transport_unknown"
                current.error_message = type(exc).__name__
        return

    if result.status == "filled" and result.average_price is not None:
        await _apply_exchange_fill(
            order_id,
            exchange_oid=result.oid,
            filled_size=result.filled_size,
            average_price=result.average_price,
            terminal=True,
        )
        return
    async with get_db_session() as db:
        current = await db.get(CopyExecutionOrder, order_id, with_for_update=True)
        if current is None:
            return
        current.exchange_oid = result.oid
        current.status = result.status
        current.error_message = result.error
        current.submitted_at = datetime.now(tz=UTC).replace(tzinfo=None)
        if result.status in ("failed", "cancelled"):
            current.completed_at = datetime.now(tz=UTC).replace(tzinfo=None)


async def recover_intent(order_id: int) -> None:
    async with get_db_session() as db:
        order = await db.get(CopyExecutionOrder, order_id)
        if order is None or order.status not in ACTIVE_INTENT_STATUSES:
            return
        if order.status == "pending":
            should_execute = True
        else:
            should_execute = False
        state = await db.get(CopyAccountExecutionState, order.user_id)
        if state is None or state.account_address is None:
            return
        owner = state.account_address
        lookup = order.exchange_oid if order.exchange_oid is not None else order.cloid
    if should_execute:
        await execute_intent(order_id)
        return
    status = await HyperliquidExchangeClient().get_order_status(owner, lookup)
    if status == "open":
        async with get_db_session() as db:
            current = await db.get(CopyExecutionOrder, order_id, with_for_update=True)
            if current is not None:
                current.status = "submitted"
        return

    fills = await HyperliquidInfoClient().get_fills(owner, limit=None)
    matching = [
        fill
        for fill in fills
        if order.exchange_oid is not None and fill.oid == order.exchange_oid
    ]
    total_filled = sum((fill.sz for fill in matching), start=Decimal("0"))
    if matching:
        average = sum(
            (fill.sz * fill.px for fill in matching),
            start=Decimal("0"),
        ) / total_filled
        await _apply_exchange_fill(
            order_id,
            exchange_oid=order.exchange_oid,
            filled_size=total_filled,
            average_price=average,
            terminal=status == "filled",
        )
    if status in ("cancelled", "filled"):
        async with get_db_session() as db:
            current = await db.get(CopyExecutionOrder, order_id, with_for_update=True)
            if current is not None:
                current.status = "filled" if status == "filled" else "cancelled"
                current.completed_at = datetime.now(tz=UTC).replace(tzinfo=None)


async def reconcile_market(user_id: int, dex: str, coin: str) -> None:
    if not settings.copy_engine_v2_live_enabled:
        return
    active_intent = await _active_intent_id(user_id, dex, coin)
    if active_intent is not None:
        await recover_intent(active_intent)
        return

    try:
        registry = MarketRegistry()
        registry_snapshot = await registry.get_snapshot()
        market = await registry.require_market(dex, coin)
        async with get_db_session() as db:
            state = await db.get(CopyAccountExecutionState, user_id)
            if (
                state is None
                or state.status != "active"
                or state.account_address is None
            ):
                return
            account_address = state.account_address
        account = await AccountStateReader().read(account_address, registry_snapshot)
        actual = next(
            (
                position.szi
                for state in account.dex_states
                if state.dex == dex
                for position in state.positions
                if position.coin == coin
            ),
            Decimal("0"),
        )
    except Exception as exc:
        async with get_db_session() as db:
            result = await db.execute(
                select(CopyAccountPosition).where(
                    CopyAccountPosition.user_id == user_id,
                    CopyAccountPosition.dex == dex,
                    CopyAccountPosition.coin == coin,
                )
            )
            position = result.scalar_one_or_none()
            if position is not None:
                position.status = "stalled"
                position.reason = "exchange_state_unavailable"
        logger.warning("copy_reconcile_state_unavailable", error=str(exc))
        return

    async with get_db_session() as db:
        await lock_market(db, user_id, dex, coin)
        result = await db.execute(
            select(CopyAccountPosition)
            .where(
                CopyAccountPosition.user_id == user_id,
                CopyAccountPosition.dex == dex,
                CopyAccountPosition.coin == coin,
            )
            .with_for_update()
        )
        position = result.scalar_one_or_none()
        if position is None:
            return
        expected = _decimal(position.confirmed_actual_size)
        if actual != expected:
            await block_account(
                db,
                user_id,
                "unexplained_position_delta",
                {"dex": dex, "coin": coin},
            )
            position.status = "blocked"
            position.reason = "unexplained_position_delta"
            return
        risk_result = await db.execute(
            select(CopyPositionTarget, Subscription)
            .join(
                Subscription,
                Subscription.id == CopyPositionTarget.subscription_id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.is_demo.is_(False),
                Subscription.engine_version == 2,
                Subscription.execution_status.in_(("active", "stopping")),
                CopyPositionTarget.state.in_(("active", "zero", "stopping")),
            )
        )
        risk_rows = list(risk_result.all())
        risk_allocations: list[RiskAllocation] = []
        market_target = Decimal("0")
        for target, subscription in risk_rows:
            target_market = registry_snapshot.markets.get(
                f"{target.dex}|{target.coin}"
            )
            if target_market is None or target_market.mid is None:
                position.status = "stalled"
                position.reason = "risk_market_state_unavailable"
                return
            target_size = _decimal(target.target_size)
            if target.dex == dex and target.coin == coin:
                market_target += target_size
            risk_allocations.append(
                RiskAllocation(
                    target_size=target_size,
                    price=target_market.mid,
                    subscription_max_leverage=_decimal(
                        subscription.max_leverage
                    ),
                    market_max_leverage=Decimal(target_market.max_leverage),
                )
            )
        if not risk_increase_allowed(
            before_size=actual,
            target_size=market_target,
            required_margin_usd=required_initial_margin(risk_allocations),
            collateral_usd=account.equity_usd,
        ):
            position.status = "stalled"
            position.reason = "insufficient_copy_account_collateral"
            return
        try:
            prepared = await prepare_market_intent(
                db,
                position=position,
                actual_size=actual,
                market=market,
            )
        except OpposingTargetsError:
            await block_account(
                db,
                user_id,
                "opposing_subscription_targets",
                {"dex": dex, "coin": coin},
            )
            position.status = "blocked"
            position.reason = "opposing_subscription_targets"
            return
        position.last_reconciled_at = datetime.now(tz=UTC).replace(tzinfo=None)
        order_id = prepared.order_id
    if order_id is not None:
        await execute_intent(order_id)

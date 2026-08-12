import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.copy_execution import (
    CopyExecutionAllocation,
    CopyExecutionOrder,
    CopyPositionTarget,
)
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.services.copy_engine.locking import advisory_xact_lock
from app.services.copy_engine.market_identity import market_id
from app.services.copy_engine.market_registry import MarketRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class _DemoPosition:
    size: Decimal
    average_entry: Decimal | None


async def _current_demo_position(
    subscription_id: int,
    dex: str,
    coin: str,
) -> _DemoPosition:
    async with get_db_session() as db:
        result = await db.execute(
            select(UserTrade)
            .where(
                UserTrade.subscription_id == subscription_id,
                UserTrade.dex == dex,
                UserTrade.coin == coin,
                UserTrade.is_demo.is_(True),
                UserTrade.status == "filled",
            )
            .order_by(UserTrade.executed_at, UserTrade.id)
        )
        trades = list(result.scalars().all())
    size = Decimal("0")
    average: Decimal | None = None
    for trade in trades:
        trade_size = Decimal(str(trade.size or 0))
        price = Decimal(str(trade.price or 0))
        signed = trade_size if trade.side == "long" else -trade_size
        if trade.trade_type == "close":
            signed = -signed
        new_size = size + signed
        if size == 0 or size * signed > 0:
            total = abs(size) + abs(signed)
            average = (
                ((average or price) * abs(size) + price * abs(signed)) / total
                if total
                else None
            )
        elif new_size == 0 or size * new_size <= 0:
            average = price if new_size else None
        size = new_size
    return _DemoPosition(size=size, average_entry=average)


def _cloid(subscription_id: int, target_id: int, target_version: int) -> str:
    raw = f"demo:{subscription_id}:{target_id}:{target_version}".encode()
    return "0x" + hashlib.blake2b(raw, digest_size=16).hexdigest()


async def reconcile_demo_targets(
    subscription_id: int,
    market_keys: list[str] | None = None,
) -> int:
    registry = MarketRegistry()
    snapshot = await registry.get_snapshot()
    changed = 0

    async with get_db_session() as db:
        subscription = await db.get(Subscription, subscription_id)
        if (
            subscription is None
            or not subscription.is_demo
            or subscription.engine_version != 2
            or subscription.execution_status not in ("active", "stopping")
            or (
                subscription.execution_status == "active" and not subscription.is_active
            )
        ):
            return 0
        await advisory_xact_lock(db, "demo", subscription_id)
        result = await db.execute(
            select(CopyPositionTarget)
            .where(CopyPositionTarget.subscription_id == subscription_id)
            .with_for_update()
        )
        targets = list(result.scalars().all())

        for target in targets:
            key = market_id(target.dex, target.coin).key
            if market_keys is not None and key not in market_keys:
                continue
            before = Decimal(str(target.confirmed_allocated_size))
            desired = Decimal(str(target.target_size))
            if before == desired:
                continue
            spec = snapshot.markets.get(key)
            if spec is None or spec.mid is None:
                logger.warning("demo_target_market_unavailable", market=key)
                continue
            idempotency_key = (
                f"demo:{subscription_id}:{target.id}:{target.target_version}"
            )
            existing = await db.execute(
                select(CopyExecutionOrder.id).where(
                    CopyExecutionOrder.idempotency_key == idempotency_key
                )
            )
            if existing.first() is not None:
                continue
            order = CopyExecutionOrder(
                user_id=subscription.user_id,
                dex=target.dex,
                coin=target.coin,
                kind="internal_reallocation",
                cloid=_cloid(subscription_id, target.id, target.target_version),
                aggregate_target_version=target.target_version,
                before_size=before,
                target_size=desired,
                requested_delta=desired - before,
                is_buy=desired - before > 0,
                reduce_only=abs(desired) < abs(before),
                rounded_size=abs(desired - before),
                reference_price=spec.mid,
                limit_price=spec.mid,
                status="filled",
                filled_size=abs(desired - before),
                average_price=spec.mid,
                idempotency_key=idempotency_key,
            )
            db.add(order)
            await db.flush()
            allocation = CopyExecutionAllocation(
                execution_order_id=order.id,
                target_id=target.id,
                subscription_id=subscription_id,
                requested_delta=desired - before,
                filled_delta=desired - before,
                allocation_price=spec.mid,
            )
            db.add(allocation)
            await db.flush()

            position = await _current_demo_position(
                subscription_id,
                target.dex,
                target.coin,
            )
            remaining_before = before
            if before != 0 and (desired == 0 or before * desired <= 0):
                close_size = abs(before)
            elif before != 0 and before * desired > 0 and abs(desired) < abs(before):
                close_size = abs(before) - abs(desired)
            else:
                close_size = Decimal("0")
            if close_size:
                direction = Decimal("1") if before > 0 else Decimal("-1")
                pnl = (
                    (spec.mid - position.average_entry) * close_size * direction
                    if position.average_entry is not None
                    else Decimal("0")
                )
                db.add(
                    UserTrade(
                        subscription_id=subscription_id,
                        signal_id=target.source_signal_id,
                        execution_order_id=order.id,
                        execution_allocation_id=allocation.id,
                        dex=target.dex,
                        coin=target.coin,
                        side="long" if before > 0 else "short",
                        size=close_size,
                        price=spec.mid,
                        trade_type="close",
                        realized_pnl=pnl,
                        status="filled",
                        is_demo=True,
                    )
                )
                remaining_before -= direction * close_size
            open_size = abs(desired - remaining_before)
            if open_size:
                db.add(
                    UserTrade(
                        subscription_id=subscription_id,
                        signal_id=target.source_signal_id,
                        execution_order_id=order.id,
                        execution_allocation_id=allocation.id,
                        dex=target.dex,
                        coin=target.coin,
                        side="long" if desired > 0 else "short",
                        size=open_size,
                        price=spec.mid,
                        trade_type="open",
                        status="filled",
                        is_demo=True,
                    )
                )
            target.confirmed_allocated_size = desired
            changed += 1
    return changed


async def simulate_demo_trade(signal_id: int, subscription_id: int) -> None:
    """Fail-safe compatibility entrypoint: v2 signals only update durable targets."""
    from app.services.copy_engine.target_service import process_signal_target

    await process_signal_target(signal_id, subscription_id)

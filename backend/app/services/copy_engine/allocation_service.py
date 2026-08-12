from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import (
    CopyExecutionAllocation,
    CopyExecutionOrder,
    CopyPositionTarget,
)
from app.models.trade import UserTrade
from app.models.subscription import Subscription
from app.models.new_wallet import UserNewWalletItem
from app.services.copy_engine.execution_state import utcnow_naive


def _decimal(value: object | None) -> Decimal:
    return Decimal(str(value or 0))


async def apply_filled_delta(
    db: AsyncSession,
    order: CopyExecutionOrder,
    signed_fill_delta: Decimal,
    price: Decimal,
) -> None:
    result = await db.execute(
        select(CopyExecutionAllocation, CopyPositionTarget)
        .join(
            CopyPositionTarget,
            CopyPositionTarget.id == CopyExecutionAllocation.target_id,
        )
        .where(CopyExecutionAllocation.execution_order_id == order.id)
        .with_for_update()
    )
    rows = list(result.all())
    eligible = [
        (allocation, target)
        for allocation, target in rows
        if _decimal(allocation.requested_delta) * signed_fill_delta > 0
    ]
    total_requested = sum(
        (abs(_decimal(allocation.requested_delta)) for allocation, _ in eligible),
        start=Decimal("0"),
    )
    remaining = signed_fill_delta
    for index, (allocation, target) in enumerate(eligible):
        if index == len(eligible) - 1:
            allocated = remaining
        else:
            share = abs(_decimal(allocation.requested_delta)) / total_requested
            allocated = signed_fill_delta * share
            remaining -= allocated
        before = _decimal(target.confirmed_allocated_size)
        after = before + allocated
        target.confirmed_allocated_size = after
        allocation.filled_delta = _decimal(allocation.filled_delta) + allocated
        allocation.allocation_price = price

        same_direction = before == 0 or before * after >= 0
        increasing = same_direction and abs(after) > abs(before)
        db.add(
            UserTrade(
                subscription_id=allocation.subscription_id,
                signal_id=target.source_signal_id,
                execution_order_id=order.id,
                execution_allocation_id=allocation.id,
                hl_order_id=order.exchange_oid,
                dex=order.dex,
                coin=order.coin,
                side="long" if allocated > 0 else "short",
                size=abs(allocated),
                price=price,
                trade_type="open" if increasing else "close",
                status="filled",
                is_demo=False,
            )
        )
        if after == _decimal(target.target_size) == 0 and target.state == "stopping":
            target.state = "zero"
    await _finalize_stopping_subscriptions(db, {target.subscription_id for _, target in rows})


async def apply_internal_reallocation(
    db: AsyncSession,
    order: CopyExecutionOrder,
) -> None:
    result = await db.execute(
        select(CopyExecutionAllocation, CopyPositionTarget)
        .join(
            CopyPositionTarget,
            CopyPositionTarget.id == CopyExecutionAllocation.target_id,
        )
        .where(CopyExecutionAllocation.execution_order_id == order.id)
        .with_for_update()
    )
    rows = list(result.all())
    for allocation, target in rows:
        delta = _decimal(allocation.requested_delta)
        allocation.filled_delta = delta
        allocation.allocation_price = order.reference_price
        target.confirmed_allocated_size = (
            _decimal(target.confirmed_allocated_size) + delta
        )
        if (
            _decimal(target.confirmed_allocated_size) == 0
            and _decimal(target.target_size) == 0
            and target.state == "stopping"
        ):
            target.state = "zero"
    await _finalize_stopping_subscriptions(db, {target.subscription_id for _, target in rows})


async def _finalize_stopping_subscriptions(
    db: AsyncSession,
    subscription_ids: set[int],
) -> None:
    for subscription_id in subscription_ids:
        subscription = await db.get(Subscription, subscription_id)
        if subscription is None or subscription.execution_status != "stopping":
            continue
        result = await db.execute(
            select(CopyPositionTarget.id).where(
                CopyPositionTarget.subscription_id == subscription_id,
                (
                    (CopyPositionTarget.target_size != 0)
                    | (CopyPositionTarget.confirmed_allocated_size != 0)
                ),
            )
        )
        if result.first() is not None:
            continue
        subscription.execution_status = "stopped"
        if subscription.ended_reason == "new_wallet_ttl_expired":
            item_result = await db.execute(
                select(UserNewWalletItem).where(
                    UserNewWalletItem.subscription_id == subscription.id
                )
            )
            item = item_result.scalar_one_or_none()
            if item is not None:
                item.status = "expired"
                item.ended_at = utcnow_naive()

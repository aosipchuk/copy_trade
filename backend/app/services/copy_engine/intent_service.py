import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import (
    CopyAccountPosition,
    CopyExecutionAllocation,
    CopyExecutionOrder,
    CopyPositionTarget,
)
from app.models.subscription import Subscription
from app.services.copy_engine.allocation_service import apply_internal_reallocation
from app.services.copy_engine.market_registry import MarketSpec
from app.services.copy_engine.order_builder import delta_to_order

ACTIVE_INTENT_STATUSES = ("pending", "submitted", "partial", "unknown")


class OpposingTargetsError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedIntent:
    order_id: int | None
    aggregate_target: Decimal
    actual_size: Decimal
    dust: bool = False


def _decimal(value: object | None) -> Decimal:
    return Decimal(str(value or 0))


def _identity(
    user_id: int,
    dex: str,
    coin: str,
    version: int,
    before: Decimal,
    target: Decimal,
) -> tuple[str, str]:
    raw = f"{user_id}:{dex}:{coin}:{version}:{before}:{target}"
    idempotency = hashlib.sha256(raw.encode()).hexdigest()
    cloid = "0x" + hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()
    return idempotency, cloid


async def prepare_market_intent(
    db: AsyncSession,
    *,
    position: CopyAccountPosition,
    actual_size: Decimal,
    market: MarketSpec,
    kind: str = "exchange",
) -> PreparedIntent:
    result = await db.execute(
        select(CopyPositionTarget, Subscription)
        .join(Subscription, Subscription.id == CopyPositionTarget.subscription_id)
        .where(
            Subscription.user_id == position.user_id,
            Subscription.is_demo.is_(False),
            Subscription.engine_version == 2,
            Subscription.execution_status.in_(("active", "stopping")),
            CopyPositionTarget.dex == position.dex,
            CopyPositionTarget.coin == position.coin,
            CopyPositionTarget.state.in_(("active", "zero", "stopping")),
        )
        .with_for_update()
    )
    rows = list(result.all())
    targets = [target for target, _ in rows]
    nonzero_signs = {
        1 if _decimal(target.target_size) > 0 else -1
        for target in targets
        if _decimal(target.target_size) != 0
    }
    if len(nonzero_signs) > 1:
        raise OpposingTargetsError("opposing_subscription_targets")
    aggregate = sum(
        (_decimal(target.target_size) for target in targets),
        start=Decimal("0"),
    )
    version = max((target.target_version for target in targets), default=0)
    position.aggregate_target_size = aggregate
    position.confirmed_actual_size = actual_size
    position.target_version = version

    active_result = await db.execute(
        select(CopyExecutionOrder.id).where(
            CopyExecutionOrder.user_id == position.user_id,
            CopyExecutionOrder.dex == position.dex,
            CopyExecutionOrder.coin == position.coin,
            CopyExecutionOrder.kind.in_(("exchange", "emergency")),
            CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES),
        )
    )
    active_id = active_result.scalar_one_or_none()
    if active_id is not None:
        position.status = "pending"
        return PreparedIntent(active_id, aggregate, actual_size)

    allocation_deltas = [
        (target, _decimal(target.target_size) - _decimal(target.confirmed_allocated_size))
        for target in targets
    ]
    delta = aggregate - actual_size
    if delta == 0:
        if any(value != 0 for _, value in allocation_deltas):
            idempotency, cloid = _identity(
                position.user_id,
                position.dex,
                position.coin,
                version,
                actual_size,
                aggregate,
            )
            order = CopyExecutionOrder(
                user_id=position.user_id,
                dex=position.dex,
                coin=position.coin,
                kind="internal_reallocation",
                cloid=cloid,
                aggregate_target_version=version,
                before_size=actual_size,
                target_size=aggregate,
                requested_delta=0,
                is_buy=False,
                reduce_only=False,
                rounded_size=0,
                reference_price=market.mid,
                limit_price=market.mid,
                status="filled",
                filled_size=0,
                average_price=market.mid,
                idempotency_key="internal:" + idempotency,
            )
            db.add(order)
            await db.flush()
            for target, requested in allocation_deltas:
                if requested:
                    db.add(
                        CopyExecutionAllocation(
                            execution_order_id=order.id,
                            target_id=target.id,
                            subscription_id=target.subscription_id,
                            requested_delta=requested,
                            filled_delta=0,
                            allocation_price=market.mid,
                        )
                    )
            await db.flush()
            await apply_internal_reallocation(db, order)
        position.status = "clean"
        position.pending_explained_delta = 0
        return PreparedIntent(None, aggregate, actual_size)

    order_params = delta_to_order(
        coin=position.coin,
        asset_index=market.asset_id,
        before_size=actual_size,
        target_size=aggregate,
        mid_price=market.mid or Decimal("0"),
        sz_decimals=market.sz_decimals,
    )
    if order_params is None:
        position.status = "clean"
        position.reason = "residual_below_minimum"
        return PreparedIntent(None, aggregate, actual_size, dust=True)

    effective_delta = (
        order_params.size if order_params.is_buy else -order_params.size
    )
    idempotency, cloid = _identity(
        position.user_id,
        position.dex,
        position.coin,
        version,
        actual_size,
        aggregate,
    )
    order = CopyExecutionOrder(
        user_id=position.user_id,
        dex=position.dex,
        coin=position.coin,
        kind=kind,
        cloid=cloid,
        aggregate_target_version=version,
        before_size=actual_size,
        target_size=aggregate,
        requested_delta=effective_delta,
        is_buy=order_params.is_buy,
        reduce_only=order_params.reduce_only,
        rounded_size=order_params.size,
        reference_price=market.mid,
        limit_price=order_params.limit_px,
        status="pending",
        idempotency_key=idempotency,
    )
    db.add(order)
    await db.flush()

    eligible = [
        (target, requested)
        for target, requested in allocation_deltas
        if requested * effective_delta > 0
    ]
    total = sum((abs(requested) for _, requested in eligible), Decimal("0"))
    remaining = effective_delta
    for index, (target, requested) in enumerate(eligible):
        allocated = (
            remaining
            if index == len(eligible) - 1
            else effective_delta * abs(requested) / total
        )
        remaining -= allocated
        db.add(
            CopyExecutionAllocation(
                execution_order_id=order.id,
                target_id=target.id,
                subscription_id=target.subscription_id,
                requested_delta=allocated,
                filled_delta=0,
            )
        )
    position.status = "pending"
    position.pending_explained_delta = effective_delta
    return PreparedIntent(order.id, aggregate, actual_size)

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import CopyAccountPosition, CopyPositionTarget
from app.models.subscription import Subscription
from app.services.copy_engine.locking import advisory_xact_lock


async def stop_subscription_targets(
    db: AsyncSession,
    subscription: Subscription,
    *,
    reason: str,
) -> int:
    await advisory_xact_lock(db, "subscription-targets", subscription.id)
    result = await db.execute(
        select(CopyPositionTarget)
        .where(CopyPositionTarget.subscription_id == subscription.id)
        .with_for_update()
    )
    targets = list(result.scalars().all())
    nonzero = [target for target in targets if Decimal(str(target.target_size)) != 0]
    next_version = max((target.target_version for target in targets), default=0) + 1
    for target in targets:
        if Decimal(str(target.target_size)) == 0:
            continue
        target.raw_target_size = 0
        target.target_size = 0
        target.target_notional_usd = 0
        target.state = "stopping"
        target.reason = reason
        target.target_version = next_version
        if not subscription.is_demo:
            statement = (
                pg_insert(CopyAccountPosition)
                .values(
                    user_id=subscription.user_id,
                    dex=target.dex,
                    coin=target.coin,
                    status="dirty",
                    target_version=next_version,
                )
                .on_conflict_do_update(
                    constraint="uq_copy_account_position_market",
                    set_={
                        "status": "dirty",
                        "target_version": next_version,
                        "reason": reason,
                    },
                )
            )
            await db.execute(statement)
    subscription.is_active = False
    subscription.execution_status = "stopping" if nonzero else "stopped"
    subscription.pause_reason = reason
    return len(nonzero)

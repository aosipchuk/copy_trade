from sqlalchemy import select

from app.core.database import get_db_session
from app.models.copy_execution import CopyPositionTarget
from app.models.subscription import Subscription
from app.services.copy_engine.demo_executor import reconcile_demo_targets


async def reconcile_async() -> int:
    """Repair v2 demo targets after restart without inferring from v1 trades."""
    async with get_db_session() as db:
        result = await db.execute(
            select(Subscription.id)
            .join(
                CopyPositionTarget,
                CopyPositionTarget.subscription_id == Subscription.id,
            )
            .where(
                Subscription.is_demo.is_(True),
                Subscription.engine_version == 2,
                Subscription.is_active.is_(True),
                Subscription.execution_status == "active",
                CopyPositionTarget.target_size
                != CopyPositionTarget.confirmed_allocated_size,
            )
            .distinct()
        )
        subscription_ids = list(result.scalars().all())
    return sum(
        [
            await reconcile_demo_targets(subscription_id)
            for subscription_id in subscription_ids
        ]
    )

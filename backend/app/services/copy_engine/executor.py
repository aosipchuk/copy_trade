from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.services.copy_engine.emergency import emergency_close_all
from app.services.copy_engine.lifecycle import stop_subscription_targets
from app.services.copy_engine.target_service import process_signal_target

logger = get_logger(__name__)


async def execute_copy_trade(signal_id: int, subscription_id: int) -> None:
    """Compatibility boundary that cannot execute a legacy order directly."""
    async with get_db_session() as db:
        signal = await db.get(Signal, signal_id)
        if signal is None:
            return
        if signal.engine_version != 2:
            signal.dispatch_status = "skipped_legacy"
            logger.warning("legacy_executor_call_blocked", signal_id=signal_id)
            return
    await process_signal_target(signal_id, subscription_id)


async def close_all_positions_for_user(user_id: int) -> int:
    result = await emergency_close_all(user_id)
    return len(result.accepted_intent_ids)


async def close_positions_for_subscription(
    user_id: int,
    subscription_id: int,
) -> None:
    async with get_db_session() as db:
        subscription = await db.get(Subscription, subscription_id)
        if subscription is None or subscription.user_id != user_id:
            return
        await stop_subscription_targets(
            db,
            subscription,
            reason="subscription_close_requested",
        )

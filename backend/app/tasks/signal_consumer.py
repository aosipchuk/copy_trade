import asyncio

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.signal import Signal
from app.services.copy_engine.target_service import process_signal_target
from app.services.portfolio.subscription_lifecycle import (
    executable_subscription_targets_for_signal,
)

logger = get_logger(__name__)


async def _get_active_subscription_ids(signal_id: int) -> list[int]:
    async with get_db_session() as db:
        targets = await executable_subscription_targets_for_signal(db, signal_id)
        return [target.subscription_id for target in targets]


async def fan_out_signal_async(signal_id: int) -> None:
    """Fan a durable v2 signal out to target calculation only."""
    async with get_db_session() as db:
        signal = await db.get(Signal, signal_id)
        if signal is None:
            return
        if signal.engine_version != 2:
            signal.dispatch_status = "skipped_legacy"
            logger.warning("v1_signal_blocked_at_v2_boundary", signal_id=signal_id)
            return

    subscription_ids = await _get_active_subscription_ids(signal_id)
    results = await asyncio.gather(
        *[
            process_signal_target(signal_id, subscription_id)
            for subscription_id in subscription_ids
        ],
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, Exception)]
    for error in errors:
        logger.warning("target_fan_out_error", signal_id=signal_id, error=str(error))
    logger.info(
        "target_fan_out_complete",
        signal_id=signal_id,
        subscriptions=len(subscription_ids),
        errors=len(errors),
    )

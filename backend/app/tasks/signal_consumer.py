import asyncio

from sqlalchemy import select

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.subscription import Subscription

logger = get_logger(__name__)


async def _get_active_subscriber_ids(signal_id: int) -> dict[str, list[int]]:
    """Return {'real': [...user_ids], 'demo': [...user_ids]} for this signal."""
    async with get_db_session() as db:
        from app.models.signal import Signal

        result = await db.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()
        if signal is None:
            logger.warning("fan_out_signal_not_found", signal_id=signal_id)
            return {"real": [], "demo": []}

        sub_result = await db.execute(
            select(Subscription.user_id, Subscription.is_demo).where(
                Subscription.trader_id == signal.trader_id,
                Subscription.is_active == True,  # noqa: E712
            )
        )
        rows = sub_result.all()

    real = [uid for uid, is_demo in rows if not is_demo]
    demo = [uid for uid, is_demo in rows if is_demo]
    return {"real": real, "demo": demo}


async def fan_out_signal_async(signal_id: int) -> None:
    """Find all active subscribers for signal's trader and execute trades."""
    ids = await _get_active_subscriber_ids(signal_id)

    from app.tasks.execution_tasks import (
        execute_copy_trade_async,
        simulate_demo_trade_async,
    )

    tasks = [execute_copy_trade_async(signal_id, uid) for uid in ids["real"]]
    tasks += [simulate_demo_trade_async(signal_id, uid) for uid in ids["demo"]]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    for exc in errors:
        logger.warning("fan_out_trade_error", signal_id=signal_id, error=str(exc))
    if errors:
        logger.warning("fan_out_partial_errors", signal_id=signal_id, count=len(errors))

    logger.info(
        "fan_out_dispatched",
        signal_id=signal_id,
        real=len(ids["real"]),
        demo=len(ids["demo"]),
    )

import asyncio

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.services.portfolio.subscription_lifecycle import (
    executable_subscription_targets_for_signal,
)

logger = get_logger(__name__)


async def _get_active_subscription_targets(signal_id: int) -> dict[str, list[int]]:
    """Return {'real': [...subscription_ids], 'demo': [...subscription_ids]}."""
    async with get_db_session() as db:
        targets = await executable_subscription_targets_for_signal(db, signal_id)
        if not targets:
            return {"real": [], "demo": []}

    real = [target.subscription_id for target in targets if not target.is_demo]
    demo = [target.subscription_id for target in targets if target.is_demo]
    return {"real": real, "demo": demo}


async def fan_out_signal_async(signal_id: int) -> None:
    """Find all active subscribers for signal's trader and execute trades."""
    ids = await _get_active_subscription_targets(signal_id)

    from app.tasks.execution_tasks import (
        execute_copy_trade_async,
        simulate_demo_trade_async,
    )

    tasks = [
        execute_copy_trade_async(signal_id, subscription_id)
        for subscription_id in ids["real"]
    ]
    tasks += [
        simulate_demo_trade_async(signal_id, subscription_id)
        for subscription_id in ids["demo"]
    ]

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

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.signal import Signal
from app.services.signal_detector import SignalEvent

logger = get_logger(__name__)


async def save_signals(
    db: AsyncSession,
    trader_id: int,
    trader_address: str,
    events: list[SignalEvent],
) -> list[int]:
    """
    Persist detected signal events to PostgreSQL and return saved signal IDs.
    Caller is responsible for dispatching execution tasks.
    """
    signal_ids: list[int] = []

    for event in events:
        sig = Signal(
            trader_id=trader_id,
            signal_type=event.signal_type.value,
            coin=event.coin,
            side=event.side,
            size=event.size,
            entry_price=event.entry_price,
            leverage=event.leverage,
        )
        db.add(sig)
        await db.flush()
        await db.refresh(sig)
        signal_ids.append(sig.id)
        logger.info(
            "signal_saved",
            signal_id=sig.id,
            type=event.signal_type,
            coin=event.coin,
            side=event.side,
            trader=trader_address,
        )

    return signal_ids

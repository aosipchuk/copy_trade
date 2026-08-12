from sqlalchemy import select

from app.core.database import get_db_session
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyExecutionOrder,
)
from app.services.copy_engine.execution_state import block_account
from app.services.copy_engine.reconciler import recover_intent
from app.services.hyperliquid.info_client import HyperliquidInfoClient


async def ingest_account_fills(user_id: int) -> int:
    async with get_db_session() as db:
        state = await db.get(CopyAccountExecutionState, user_id)
        if state is None or state.account_address is None:
            return 0
        account_address = state.account_address
        cursor = state.fill_cursor_ms
    fills = await HyperliquidInfoClient().get_fills(account_address, limit=None)
    relevant = [fill for fill in fills if cursor is None or fill.time > cursor]
    processed = 0
    for fill in sorted(relevant, key=lambda item: (item.time, item.tid or 0)):
        async with get_db_session() as db:
            order_result = await db.execute(
                select(CopyExecutionOrder.id).where(
                    CopyExecutionOrder.user_id == user_id,
                    CopyExecutionOrder.exchange_oid == fill.oid,
                )
            )
            order_id = order_result.scalar_one_or_none()
            state = await db.get(CopyAccountExecutionState, user_id)
            if state is None:
                return processed
            if order_id is None:
                await block_account(
                    db,
                    user_id,
                    "unknown_exchange_fill",
                    {
                        "coin": fill.coin,
                        "time_ms": fill.time,
                        "exchange_oid": fill.oid,
                    },
                )
                return processed
        await recover_intent(order_id)
        processed += 1
    if relevant:
        async with get_db_session() as db:
            state = await db.get(CopyAccountExecutionState, user_id)
            if state is not None:
                state.fill_cursor_ms = max(fill.time for fill in relevant)
    return processed

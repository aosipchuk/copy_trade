from decimal import Decimal

from sqlalchemy import select

from app.core.database import get_db_session
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyAccountPosition,
    CopyExecutionOrder,
)
from app.services.copy_engine.account_state import AccountStateReader
from app.services.copy_engine.execution_state import block_account
from app.services.copy_engine.intent_service import ACTIVE_INTENT_STATUSES
from app.services.copy_engine.market_identity import market_id
from app.services.copy_engine.market_registry import MarketRegistry


def _decimal(value: object | None) -> Decimal:
    return Decimal(str(value or 0))


async def detect_account_drift(user_id: int) -> bool:
    async with get_db_session() as db:
        state = await db.get(CopyAccountExecutionState, user_id)
        if (
            state is None
            or state.status != "active"
            or state.account_address is None
        ):
            return False
        account_address = state.account_address
    try:
        registry = await MarketRegistry().get_snapshot()
        snapshot = await AccountStateReader().read(account_address, registry)
    except Exception:
        # An outage is retryable uncertainty, not evidence of external activity.
        return False

    async with get_db_session() as db:
        known_result = await db.execute(
            select(
                CopyExecutionOrder.exchange_oid,
                CopyExecutionOrder.cloid,
            ).where(
                CopyExecutionOrder.user_id == user_id,
                CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES),
            )
        )
        known_oids = {row[0] for row in known_result.all() if row[0] is not None}
        known_result = await db.execute(
            select(CopyExecutionOrder.cloid).where(
                CopyExecutionOrder.user_id == user_id,
                CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES),
            )
        )
        known_cloids = set(known_result.scalars().all())
        for dex, order in snapshot.open_orders:
            if order.oid not in known_oids and order.cloid not in known_cloids:
                await block_account(
                    db,
                    user_id,
                    "unknown_open_order",
                    {
                        "dex": dex,
                        "coin": order.coin,
                        "exchange_oid": order.oid,
                        "cloid": order.cloid,
                    },
                )
                return True

        actual_by_key = {
            market_id(dex, position.coin).key: position.szi
            for dex, position in snapshot.positions
        }
        position_result = await db.execute(
            select(CopyAccountPosition).where(
                CopyAccountPosition.user_id == user_id
            )
        )
        positions = list(position_result.scalars().all())
        known_keys = {
            market_id(position.dex, position.coin).key for position in positions
        }
        for position in positions:
            key = market_id(position.dex, position.coin).key
            actual = actual_by_key.get(key, Decimal("0"))
            if actual == _decimal(position.confirmed_actual_size):
                continue
            active_result = await db.execute(
                select(CopyExecutionOrder.id).where(
                    CopyExecutionOrder.user_id == user_id,
                    CopyExecutionOrder.dex == position.dex,
                    CopyExecutionOrder.coin == position.coin,
                    CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES),
                )
            )
            if active_result.first() is None:
                await block_account(
                    db,
                    user_id,
                    "unexplained_position_delta",
                    {"dex": position.dex, "coin": position.coin},
                )
                return True
        unknown_position_keys = set(actual_by_key) - known_keys
        if unknown_position_keys:
            key = sorted(unknown_position_keys)[0]
            market = registry.markets[key]
            await block_account(
                db,
                user_id,
                "unknown_position",
                {"dex": market.dex, "coin": market.coin},
            )
            return True
    return False

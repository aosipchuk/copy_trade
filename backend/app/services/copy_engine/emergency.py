from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import get_db_session
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyAccountPosition,
    CopyPositionTarget,
)
from app.models.subscription import Subscription
from app.models.user import User
from app.services.copy_engine.account_state import AccountStateReader
from app.services.copy_engine.execution_state import pause_account
from app.services.copy_engine.intent_service import prepare_market_intent
from app.services.copy_engine.locking import lock_market
from app.services.copy_engine.market_identity import market_id
from app.services.copy_engine.market_registry import MarketRegistry
from app.services.copy_engine.reconciler import execute_intent


@dataclass(frozen=True)
class EmergencyResult:
    subscription_count: int
    position_count: int
    accepted_intent_ids: list[int]


async def emergency_close_all(user_id: int) -> EmergencyResult:
    registry = MarketRegistry()
    snapshot = await registry.get_snapshot()
    async with get_db_session() as db:
        user = await db.get(User, user_id)
        state = await db.get(CopyAccountExecutionState, user_id)
        if user is None or not user.hl_address:
            raise ValueError("No Hyperliquid wallet is configured")
        account_address = (
            state.account_address
            if state is not None and state.account_address
            else user.hl_address
        )
    account = await AccountStateReader().read(account_address, snapshot)

    async with get_db_session() as db:
        await pause_account(db, user_id, "emergency_close_all_requested")
        sub_result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.is_demo.is_(False),
            )
        )
        subscriptions = list(sub_result.scalars().all())
        for subscription in subscriptions:
            subscription.is_active = False
            subscription.execution_status = "paused"
            subscription.pause_reason = "emergency_close_all_requested"
        target_result = await db.execute(
            select(CopyPositionTarget)
            .join(Subscription, Subscription.id == CopyPositionTarget.subscription_id)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_demo.is_(False),
            )
        )
        for target in target_result.scalars().all():
            target.raw_target_size = Decimal("0")
            target.target_size = Decimal("0")
            target.target_notional_usd = Decimal("0")
            target.state = "stopping"
            target.reason = "emergency_close_all_requested"
            target.target_version += 1

    intent_ids: list[int] = []
    for dex, position in account.positions:
        market = snapshot.markets.get(market_id(dex, position.coin).key)
        if market is None or market.mid is None:
            async with get_db_session() as db:
                await pause_account(
                    db,
                    user_id,
                    "emergency_market_metadata_unavailable",
                    {"dex": dex, "coin": position.coin},
                )
            continue
        async with get_db_session() as db:
            await lock_market(db, user_id, dex, position.coin)
            statement = (
                pg_insert(CopyAccountPosition)
                .values(
                    user_id=user_id,
                    dex=dex,
                    coin=position.coin,
                    aggregate_target_size=0,
                    confirmed_actual_size=position.szi,
                    pending_explained_delta=0,
                    target_version=0,
                    status="dirty",
                )
                .on_conflict_do_update(
                    constraint="uq_copy_account_position_market",
                    set_={
                        "aggregate_target_size": Decimal("0"),
                        "confirmed_actual_size": position.szi,
                        "status": "dirty",
                    },
                )
                .returning(CopyAccountPosition.id)
            )
            position_id = (await db.execute(statement)).scalar_one()
            account_position = await db.get(CopyAccountPosition, position_id)
            if account_position is None:
                continue
            prepared = await prepare_market_intent(
                db,
                position=account_position,
                actual_size=position.szi,
                market=market,
                kind="emergency",
            )
            if prepared.order_id is not None:
                intent_ids.append(prepared.order_id)

    for intent_id in intent_ids:
        await execute_intent(intent_id)
    return EmergencyResult(
        subscription_count=len(subscriptions),
        position_count=len(account.positions),
        accepted_intent_ids=intent_ids,
    )

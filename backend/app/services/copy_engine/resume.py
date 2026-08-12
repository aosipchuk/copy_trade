from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyPositionTarget,
    TraderMarketScope,
)
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.models.user import User
from app.services.copy_engine.locking import lock_user_account
from app.services.copy_engine.market_registry import MarketRegistry
from app.services.copy_engine.preflight import run_preflight
from app.services.hyperliquid.info_client import HyperliquidInfoClient


@dataclass(frozen=True)
class ResumeResult:
    subscription: Subscription
    baseline_market_count: int
    warning: str


async def initialize_subscription_baseline(
    db: AsyncSession,
    subscription: Subscription,
    trader: Trader,
) -> int:
    scope_result = await db.execute(
        select(TraderMarketScope.dex).where(TraderMarketScope.trader_id == trader.id)
    )
    dexes = {"", *[str(dex) for dex in scope_result.scalars().all()]}
    hl = HyperliquidInfoClient()
    registry = await MarketRegistry(hl).get_snapshot()
    baseline_count = 0
    for dex in sorted(dexes):
        positions = await hl.get_positions(trader.hl_address, dex)
        for position in positions:
            key = position.coin
            market = registry.markets.get(f"{dex}|{key}")
            if market is None:
                # market_id normalization handles unprefixed default coins.
                from app.services.copy_engine.market_identity import market_id

                market = registry.markets.get(market_id(dex, key).key)
            if market is None:
                raise ValueError(f"Leader market metadata unavailable: {dex}:{key}")
            statement = (
                pg_insert(CopyPositionTarget)
                .values(
                    subscription_id=subscription.id,
                    dex=dex,
                    coin=market.coin,
                    leader_size=position.szi,
                    raw_target_size=Decimal("0"),
                    target_size=Decimal("0"),
                    confirmed_allocated_size=Decimal("0"),
                    target_notional_usd=Decimal("0"),
                    price_snapshot=market.mid,
                    sizing_mode=subscription.sizing_mode,
                    state="baseline_only",
                    target_version=1,
                    reason="existing_leader_position_not_copied",
                )
                .on_conflict_do_update(
                    constraint="uq_copy_position_target_market",
                    set_={
                        "leader_size": position.szi,
                        "raw_target_size": Decimal("0"),
                        "target_size": Decimal("0"),
                        "confirmed_allocated_size": Decimal("0"),
                        "state": "baseline_only",
                        "reason": "existing_leader_position_not_copied",
                    },
                )
            )
            await db.execute(statement)
            baseline_count += 1
    return baseline_count


async def resume_subscription(
    db: AsyncSession,
    *,
    user: User,
    subscription_id: int,
) -> ResumeResult:
    await lock_user_account(db, user.id)
    subscription = await db.get(Subscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        raise LookupError(f"Subscription {subscription_id} not found")
    if subscription.is_demo:
        raise ValueError("Demo subscriptions do not use live-account resume")
    if (
        subscription.engine_version == 2
        and subscription.execution_status == "active"
        and subscription.is_active
    ):
        baseline_result = await db.execute(
            select(CopyPositionTarget.id).where(
                CopyPositionTarget.subscription_id == subscription.id,
                CopyPositionTarget.state == "baseline_only",
            )
        )
        return ResumeResult(
            subscription=subscription,
            baseline_market_count=len(baseline_result.all()),
            warning="Existing leader positions remain baseline-only.",
        )

    preflight = await run_preflight(db, user, persist=True)
    if not preflight.ok:
        failures = [check.code for check in preflight.checks if not check.ok]
        raise ValueError("Preflight failed: " + ", ".join(failures))

    trader = await db.get(Trader, subscription.trader_id)
    if trader is None:
        raise ValueError("Subscription trader no longer exists")
    baseline_count = await initialize_subscription_baseline(db, subscription, trader)
    state = await db.get(CopyAccountExecutionState, user.id)
    if state is None:
        raise ValueError("Copy account state is missing")
    state.status = "active"
    state.reason = None
    state.details = None
    state.version += 1
    subscription.engine_version = 2
    subscription.execution_status = "active"
    subscription.pause_reason = None
    subscription.execution_status_details = None
    subscription.is_active = True
    from app.services.copy_engine.execution_state import utcnow_naive

    subscription.resumed_at = utcnow_naive()
    return ResumeResult(
        subscription=subscription,
        baseline_market_count=baseline_count,
        warning=(
            "Existing leader positions were recorded as baseline-only and will not "
            "be copied until the leader closes them and opens again."
        ),
    )

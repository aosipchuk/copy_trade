from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyAccountPosition,
    CopyPositionTarget,
)
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.services.copy_engine.account_state import AccountStateReader
from app.services.copy_engine.locking import advisory_xact_lock
from app.services.copy_engine.market_identity import allowed_market, market_id
from app.services.copy_engine.market_registry import MarketRegistry, MarketSpec
from app.services.copy_engine.target_calculator import TargetInput, calculate_targets

logger = get_logger(__name__)


def _decimal(value: object | None, default: str = "0") -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(default)


async def _live_equity(
    subscription: Subscription,
    registry: MarketRegistry,
) -> Decimal | None:
    if subscription.sizing_mode != "equity_pct":
        return None
    async with get_db_session() as db:
        state = await db.get(CopyAccountExecutionState, subscription.user_id)
        if (
            state is None
            or state.status != "active"
            or state.account_address is None
        ):
            raise ValueError("Copy account is not active")
        snapshot = await registry.get_snapshot()
        account = await AccountStateReader().read(state.account_address, snapshot)
        return account.equity_usd


async def process_signal_target(signal_id: int, subscription_id: int) -> list[str]:
    """Apply one durable leader target to a subscription target portfolio."""
    registry = MarketRegistry()
    registry_snapshot = await registry.get_snapshot()
    demo = False
    changed_market_keys: list[str] = []

    async with get_db_session() as db:
        signal = await db.get(Signal, signal_id)
        subscription = await db.get(Subscription, subscription_id)
        if signal is None or subscription is None:
            return []
        if signal.engine_version != 2:
            signal.dispatch_status = "skipped_legacy"
            return []
        demo = subscription.is_demo
        executable = (
            subscription.engine_version == 2
            and subscription.is_active
            and subscription.execution_status == "active"
        )
        if not executable:
            return []
        if not demo and not settings.copy_engine_v2_live_enabled:
            return []
        if signal.target_size is None:
            signal.dispatch_status = "failed"
            return []

        await advisory_xact_lock(db, "subscription-targets", subscription.id)
        target_result = await db.execute(
            select(CopyPositionTarget)
            .where(CopyPositionTarget.subscription_id == subscription.id)
            .with_for_update()
        )
        targets = list(target_result.scalars().all())
        target_by_key = {
            market_id(target.dex, target.coin).key: target for target in targets
        }
        current_market = market_id(signal.dex, signal.coin)
        current = target_by_key.get(current_market.key)

        if current is not None and current.state == "baseline_only":
            current.leader_size = signal.target_size
            current.source_signal_id = signal.id
            if _decimal(signal.target_size) != 0:
                signal.dispatch_status = "dispatched"
                return []
            current.state = "zero"

        if not allowed_market(subscription.allowed_coins, current_market):
            if current is not None:
                current.leader_size = signal.target_size
                current.raw_target_size = 0
                current.target_size = 0
                current.target_notional_usd = 0
                current.state = "blocked"
                current.reason = "market_not_allowed"
            signal.dispatch_status = "dispatched"
            return []

        spec_by_key: dict[str, MarketSpec] = {}
        leader_by_key: dict[str, Decimal] = {}
        for target in targets:
            key = market_id(target.dex, target.coin).key
            spec = registry_snapshot.markets.get(key)
            if spec is None or spec.mid is None:
                raise ValueError(f"Market metadata unavailable for {key}")
            spec_by_key[key] = spec
            leader_by_key[key] = _decimal(target.leader_size)
        current_spec = await registry.require_market(signal.dex, signal.coin)
        spec_by_key[current_market.key] = current_spec
        leader_by_key[current_market.key] = _decimal(signal.target_size)

        if demo:
            equity = _decimal(subscription.max_allocation_usd)
        else:
            account_state = await db.get(
                CopyAccountExecutionState,
                subscription.user_id,
            )
            if account_state is None or account_state.status != "active":
                return []
            equity = None

    if not demo and subscription.sizing_mode == "equity_pct":
        equity = await _live_equity(subscription, registry)

    calculated = calculate_targets(
        [
            TargetInput(
                dex=spec.dex,
                coin=spec.coin,
                leader_size=leader_by_key[key],
                price=spec.mid or Decimal("0"),
                sz_decimals=spec.sz_decimals,
            )
            for key, spec in sorted(spec_by_key.items())
        ],
        sizing_mode=subscription.sizing_mode,
        copy_ratio_pct=_decimal(subscription.copy_ratio_pct),
        max_allocation_usd=_decimal(subscription.max_allocation_usd),
        max_per_coin_usd=(
            _decimal(subscription.max_per_coin_usd)
            if subscription.max_per_coin_usd is not None
            else None
        ),
        equity_usd=equity,
    )

    async with get_db_session() as db:
        await advisory_xact_lock(db, "subscription-targets", subscription_id)
        subscription = await db.get(Subscription, subscription_id)
        signal = await db.get(Signal, signal_id)
        if signal is None or subscription is None:
            return []
        if not (
            subscription.engine_version == 2
            and subscription.is_active
            and subscription.execution_status == "active"
        ):
            return []
        target_result = await db.execute(
            select(CopyPositionTarget)
            .where(CopyPositionTarget.subscription_id == subscription_id)
            .with_for_update()
        )
        target_by_key = {
            market_id(target.dex, target.coin).key: target
            for target in target_result.scalars().all()
        }
        next_version = max(
            (target.target_version for target in target_by_key.values()),
            default=0,
        ) + 1

        for result in calculated:
            key = market_id(result.dex, result.coin).key
            target = target_by_key.get(key)
            old_size = _decimal(target.target_size) if target else Decimal("0")
            if target is None:
                target = CopyPositionTarget(
                    subscription_id=subscription_id,
                    dex=result.dex,
                    coin=result.coin,
                    leader_size=result.leader_size,
                    raw_target_size=result.raw_target_size,
                    target_size=result.target_size,
                    target_notional_usd=result.target_notional_usd,
                    price_snapshot=result.price,
                    sizing_mode=subscription.sizing_mode,
                    state="active" if result.target_size else "zero",
                    source_signal_id=(
                        signal.id if key == market_id(signal.dex, signal.coin).key else None
                    ),
                    target_version=next_version,
                )
                db.add(target)
            else:
                target.leader_size = result.leader_size
                target.raw_target_size = result.raw_target_size
                target.target_size = result.target_size
                target.target_notional_usd = result.target_notional_usd
                target.price_snapshot = result.price
                target.sizing_mode = subscription.sizing_mode
                target.state = "active" if result.target_size else "zero"
                target.reason = None
                target.target_version = next_version
                if key == market_id(signal.dex, signal.coin).key:
                    target.source_signal_id = signal.id
            if old_size != result.target_size:
                changed_market_keys.append(key)
                if not demo:
                    statement = (
                        pg_insert(CopyAccountPosition)
                        .values(
                            user_id=subscription.user_id,
                            dex=result.dex,
                            coin=result.coin,
                            aggregate_target_size=0,
                            confirmed_actual_size=0,
                            target_version=next_version,
                            status="dirty",
                        )
                        .on_conflict_do_update(
                            constraint="uq_copy_account_position_market",
                            set_={
                                "status": "dirty",
                                "target_version": next_version,
                                "reason": None,
                            },
                        )
                    )
                    await db.execute(statement)
        signal.dispatch_status = "dispatched"

    if demo and changed_market_keys:
        from app.services.copy_engine.demo_executor import reconcile_demo_targets

        await reconcile_demo_targets(subscription_id, changed_market_keys)
    return changed_market_keys

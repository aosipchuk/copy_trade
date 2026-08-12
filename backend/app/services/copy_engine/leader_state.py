import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import TraderMarketScope, TraderPositionState
from app.models.signal import Signal
from app.services.copy_engine.locking import advisory_xact_lock
from app.services.hyperliquid.models import Position, PositionLeverage
from app.services.signal_detector import SignalEvent, detect_changes


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _as_position(state: TraderPositionState) -> Position:
    size = Decimal(str(state.accepted_size))
    return Position(
        coin=state.coin,
        szi=size,
        entryPx=(Decimal(str(state.entry_price)) if state.entry_price else None),
        unrealized_pnl=Decimal("0"),
        leverage=PositionLeverage(
            type="cross",
            value=max(1, int(state.leverage or 1)),
        ),
    )


def _dedupe_key(
    trader_id: int,
    event: SignalEvent,
    snapshot_version: int,
) -> str:
    raw = (
        f"v2:{trader_id}:{event.dex}:{event.coin}:"
        f"{event.previous_size}:{event.target_size}:{snapshot_version}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def accept_leader_snapshot(
    db: AsyncSession,
    *,
    trader_id: int,
    dex: str,
    positions: list[Position],
    discovery_source: str = "poll",
) -> list[int]:
    """Persist a valid snapshot and durable signals in one transaction."""
    await advisory_xact_lock(db, "leader", trader_id, dex)
    now = _utcnow()
    scope = await db.get(TraderMarketScope, (trader_id, dex))
    is_baseline = scope is None or scope.last_polled_at is None
    if scope is None:
        scope = TraderMarketScope(
            trader_id=trader_id,
            dex=dex,
            discovery_source=discovery_source,
        )
        db.add(scope)

    result = await db.execute(
        select(TraderPositionState)
        .where(
            TraderPositionState.trader_id == trader_id,
            TraderPositionState.dex == dex,
        )
        .with_for_update()
    )
    existing = {state.coin: state for state in result.scalars().all()}
    accepted = [
        _as_position(state)
        for state in existing.values()
        if Decimal(str(state.accepted_size)) != 0
    ]
    events = [] if is_baseline else detect_changes(accepted, positions, dex=dex)
    event_by_coin = {event.coin: event for event in events}
    observed_by_coin = {position.coin: position for position in positions}
    next_version = (
        max(
            (state.snapshot_version for state in existing.values()),
            default=0,
        )
        + 1
    )

    for coin in sorted(existing.keys() | observed_by_coin.keys()):
        observed = observed_by_coin.get(coin)
        observed_size = observed.szi if observed else Decimal("0")
        state = existing.get(coin)
        if state is None:
            state = TraderPositionState(
                trader_id=trader_id,
                dex=dex,
                coin=coin,
                observed_size=observed_size,
                accepted_size=(observed_size if is_baseline else Decimal("0")),
                observed_at=now,
                accepted_at=now,
                snapshot_version=next_version,
            )
            db.add(state)
        else:
            state.observed_size = observed_size
            state.observed_at = now

        if observed is not None:
            state.entry_price = observed.entry_px
            state.leverage = Decimal(observed.leverage.value)
        event = event_by_coin.get(coin)
        if is_baseline or event is not None:
            state.accepted_size = observed_size
            state.accepted_at = now
            state.snapshot_version = next_version

    signal_ids: list[int] = []
    for event in events:
        statement = (
            pg_insert(Signal)
            .values(
                trader_id=trader_id,
                signal_type=event.signal_type.value,
                coin=event.coin,
                dex=dex,
                side=event.side,
                size=event.size,
                entry_price=event.entry_price,
                leverage=event.leverage,
                previous_size=event.previous_size,
                target_size=event.target_size,
                delta_size=event.delta_size,
                snapshot_version=next_version,
                engine_version=2,
                dedupe_key=_dedupe_key(trader_id, event, next_version),
                dispatch_status="accepted",
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(Signal.id)
        )
        signal_id = (await db.execute(statement)).scalar_one_or_none()
        if signal_id is not None:
            signal_ids.append(signal_id)

    scope.last_polled_at = now
    return signal_ids

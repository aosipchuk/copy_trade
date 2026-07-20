from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.new_wallet import NewWalletCandidate, NewWalletFundingLink
from app.models.trader import Trader
from app.services.hyperliquid.address import normalize_hl_address
from app.services.hyperliquid.funding_events import FundingEvent, FundingEventProvider
from app.services.new_wallets.chain import build_funding_chain
from app.services.new_wallets.types import FundingChainResult

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def upsert_candidate_from_event(
    db: AsyncSession,
    event: FundingEvent,
) -> NewWalletCandidate:
    address = normalize_hl_address(event.target_address)
    now = utcnow()
    stmt = (
        pg_insert(NewWalletCandidate)
        .values(
            hl_address=address,
            status="pending",
            detected_at=now,
            funded_at=event.event_time,
            first_seen_tx_hash=event.tx_hash,
            evidence_json={"first_event": event.raw_event},
        )
        .on_conflict_do_update(
            index_elements=["hl_address"],
            set_={
                "status": case(
                    (
                        NewWalletCandidate.status.in_(("rejected", "expired")),
                        "pending",
                    ),
                    else_=NewWalletCandidate.status,
                ),
                "funded_at": event.event_time,
                "first_seen_tx_hash": event.tx_hash,
                "last_checked_at": now,
                "reject_reason": case(
                    (
                        NewWalletCandidate.status.in_(("rejected", "expired")),
                        None,
                    ),
                    else_=NewWalletCandidate.reject_reason,
                ),
            },
        )
        .returning(NewWalletCandidate)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def qualify_candidate(
    db: AsyncSession,
    candidate: NewWalletCandidate,
    *,
    provider: FundingEventProvider,
) -> NewWalletCandidate:
    result = await build_funding_chain(candidate.hl_address, provider=provider)
    await persist_chain_result(db, candidate, result)
    return candidate


async def qualify_address(
    db: AsyncSession,
    address: str,
    *,
    provider: FundingEventProvider,
    funded_at: datetime | None = None,
    tx_hash: str | None = None,
) -> NewWalletCandidate:
    normalized = normalize_hl_address(address)
    now = utcnow()
    candidate_result = await db.execute(
        select(NewWalletCandidate).where(NewWalletCandidate.hl_address == normalized)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        candidate = NewWalletCandidate(
            hl_address=normalized,
            status="pending",
            detected_at=now,
            funded_at=funded_at,
            first_seen_tx_hash=tx_hash,
        )
        db.add(candidate)
        await db.flush()
    elif candidate.status == "disabled":
        return candidate

    result = await build_funding_chain(normalized, provider=provider)
    await persist_chain_result(db, candidate, result)
    return candidate


async def persist_chain_result(
    db: AsyncSession,
    candidate: NewWalletCandidate,
    result: FundingChainResult,
) -> None:
    now = utcnow()
    candidate.last_checked_at = now
    candidate.chain_depth = result.chain_depth
    candidate.chain_total_balance_usd = float(result.chain_total_balance_usd)
    candidate.threshold_usd_snapshot = float(result.threshold_usd)
    candidate.reject_reason = result.reject_reason
    candidate.evidence_json = result.evidence
    if result.first_event is not None:
        candidate.funded_at = result.first_event.event_time
        candidate.first_seen_tx_hash = result.first_event.tx_hash

    await db.execute(
        delete(NewWalletFundingLink).where(
            NewWalletFundingLink.candidate_id == candidate.id
        )
    )
    for link in result.links:
        db.add(
            NewWalletFundingLink(
                candidate_id=candidate.id,
                depth=link.depth,
                wallet_address=link.wallet_address,
                funded_by_address=link.funded_by_address,
                amount_usdc=(
                    float(link.amount_usdc) if link.amount_usdc is not None else None
                ),
                event_time=link.event_time,
                tx_hash=link.tx_hash,
                balance_usd=(
                    float(link.balance_usd) if link.balance_usd is not None else None
                ),
                balance_source=link.balance_source,
                raw_event_json=link.raw_event_json,
            )
        )

    if result.qualified:
        trader = await _upsert_new_wallet_trader(db, candidate.hl_address, now)
        candidate.trader_id = trader.id
        candidate.status = "qualified"
        candidate.qualified_at = now
        candidate.reject_reason = None
        logger.info(
            "new_wallet_candidate_qualified",
            candidate_id=candidate.id,
            address=candidate.hl_address,
            chain_total=float(result.chain_total_balance_usd),
            depth=result.chain_depth,
        )
        return

    candidate.status = "rejected"
    logger.info(
        "new_wallet_candidate_rejected",
        candidate_id=candidate.id,
        address=candidate.hl_address,
        reason=result.reject_reason,
        chain_total=float(result.chain_total_balance_usd),
    )


async def _upsert_new_wallet_trader(
    db: AsyncSession,
    address: str,
    now: datetime,
) -> Trader:
    stmt = (
        pg_insert(Trader)
        .values(
            hl_address=address,
            display_name=None,
            is_active=True,
            has_perp_activity=None,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["hl_address"],
            set_={
                "is_active": True,
                "has_perp_activity": None,
                "last_seen_at": now,
            },
        )
        .returning(Trader)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def pending_candidates_for_qualification(
    db: AsyncSession,
    *,
    limit: int | None = None,
) -> list[NewWalletCandidate]:
    result = await db.execute(
        select(NewWalletCandidate)
        .where(NewWalletCandidate.status == "pending")
        .order_by(NewWalletCandidate.detected_at.asc())
        .limit(limit or settings.new_wallet_max_candidates_per_run)
    )
    return list(result.scalars().all())


def discovery_start_time() -> datetime:
    return utcnow() - timedelta(hours=settings.new_wallet_discovery_lookback_hours)


async def candidate_status_counts(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(NewWalletCandidate.status, func.count(NewWalletCandidate.id)).group_by(
            NewWalletCandidate.status
        )
    )
    return {str(status): int(count) for status, count in result.all()}


def event_passes_min_amount(event: FundingEvent) -> bool:
    amount = event.amount_usdc
    if amount is None:
        return True
    return amount >= Decimal(str(settings.new_wallet_min_incoming_amount_usd))

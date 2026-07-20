from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.services.hyperliquid.address import normalize_hl_address
from app.services.hyperliquid.funding_events import (
    FundingEvent,
    FundingEventProvider,
    FundingEventProviderUnavailable,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import AccountEquitySnapshot
from app.services.new_wallets.types import FundingChainLink, FundingChainResult

logger = get_logger(__name__)


class FundingLookup(Protocol):
    async def latest_incoming_for_address(
        self,
        address: str,
        *,
        before_time: datetime | None = None,
    ) -> FundingEvent | None:
        ...


class AccountBalanceClient(Protocol):
    async def get_account_equity_usd(self, address: str) -> AccountEquitySnapshot:
        ...

    async def get_fills_by_time(
        self,
        address: str,
        *,
        start_time: int = 0,
        end_time: int | None = None,
        max_fills: int = 1,
    ) -> list[object]:
        ...

    async def get_positions(self, address: str) -> list[object]:
        ...


async def is_wallet_new_for_copying(
    address: str,
    *,
    client: AccountBalanceClient | None = None,
) -> bool:
    normalized = normalize_hl_address(address)
    hl = client or HyperliquidInfoClient()
    fills = await hl.get_fills_by_time(normalized, start_time=0, max_fills=1)
    if fills:
        return False
    positions = await hl.get_positions(normalized)
    return len(positions) == 0


async def find_latest_incoming_funding(
    address: str,
    *,
    provider: FundingLookup,
    before_time: datetime | None = None,
) -> FundingEvent | None:
    return await provider.latest_incoming_for_address(
        normalize_hl_address(address),
        before_time=before_time,
    )


async def build_funding_chain(
    target_address: str,
    *,
    provider: FundingEventProvider,
    client: AccountBalanceClient | None = None,
    threshold_usd: Decimal | None = None,
    max_depth: int | None = None,
) -> FundingChainResult:
    target = normalize_hl_address(target_address)
    threshold = threshold_usd or Decimal(
        str(settings.new_wallet_chain_balance_threshold_usd)
    )
    depth_limit = max_depth or settings.new_wallet_max_chain_depth
    hl = client or HyperliquidInfoClient()

    try:
        is_new = await is_wallet_new_for_copying(target, client=hl)
    except Exception as exc:
        logger.warning(
            "new_wallet_activity_check_failed",
            target=target,
            error=str(exc),
        )
        raise
    if not is_new:
        return FundingChainResult(
            status="rejected",
            links=[],
            chain_total_balance_usd=Decimal("0"),
            chain_depth=0,
            threshold_usd=threshold,
            reject_reason="already_trading",
            evidence={"target_address": target},
        )

    current_wallet = target
    before_time: datetime | None = None
    seen_wallets = {target}
    links: list[FundingChainLink] = []
    chain_total = Decimal("0")
    first_event: FundingEvent | None = None

    for depth in range(1, depth_limit + 1):
        try:
            event = await find_latest_incoming_funding(
                current_wallet,
                provider=provider,
                before_time=before_time,
            )
        except FundingEventProviderUnavailable:
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth - 1,
                threshold=threshold,
                reason="provider_unavailable",
                target=target,
            )

        if event is None:
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth - 1,
                threshold=threshold,
                reason="missing_funding_source",
                target=target,
            )
        if first_event is None:
            first_event = event

        if not event.source_address:
            links.append(
                FundingChainLink(
                    depth=depth,
                    wallet_address=current_wallet,
                    funded_by_address=None,
                    amount_usdc=event.amount_usdc,
                    event_time=event.event_time,
                    tx_hash=event.tx_hash,
                    balance_usd=None,
                    balance_source=None,
                    raw_event_json=event.raw_event,
                )
            )
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth,
                threshold=threshold,
                reason="missing_funding_source",
                target=target,
            )

        try:
            source_wallet = normalize_hl_address(event.source_address)
        except ValueError:
            links.append(
                FundingChainLink(
                    depth=depth,
                    wallet_address=current_wallet,
                    funded_by_address=event.source_address,
                    amount_usdc=event.amount_usdc,
                    event_time=event.event_time,
                    tx_hash=event.tx_hash,
                    balance_usd=None,
                    balance_source=None,
                    raw_event_json=event.raw_event,
                )
            )
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth,
                threshold=threshold,
                reason="source_not_wallet",
                target=target,
            )

        if source_wallet in seen_wallets:
            links.append(
                FundingChainLink(
                    depth=depth,
                    wallet_address=current_wallet,
                    funded_by_address=source_wallet,
                    amount_usdc=event.amount_usdc,
                    event_time=event.event_time,
                    tx_hash=event.tx_hash,
                    balance_usd=None,
                    balance_source=None,
                    raw_event_json=event.raw_event,
                )
            )
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth,
                threshold=threshold,
                reason="chain_cycle",
                target=target,
            )

        try:
            balance = await hl.get_account_equity_usd(source_wallet)
        except Exception as exc:
            logger.warning(
                "new_wallet_balance_fetch_failed",
                target=target,
                source=source_wallet,
                error=str(exc),
            )
            links.append(
                FundingChainLink(
                    depth=depth,
                    wallet_address=current_wallet,
                    funded_by_address=source_wallet,
                    amount_usdc=event.amount_usdc,
                    event_time=event.event_time,
                    tx_hash=event.tx_hash,
                    balance_usd=None,
                    balance_source=None,
                    raw_event_json=event.raw_event,
                )
            )
            return _rejected(
                links=links,
                total=chain_total,
                depth=depth,
                threshold=threshold,
                reason="balance_fetch_failed",
                target=target,
            )

        chain_total += balance.balance_usd
        links.append(
            FundingChainLink(
                depth=depth,
                wallet_address=current_wallet,
                funded_by_address=source_wallet,
                amount_usdc=event.amount_usdc,
                event_time=event.event_time,
                tx_hash=event.tx_hash,
                balance_usd=balance.balance_usd,
                balance_source=balance.balance_source,
                raw_event_json={
                    "event": event.raw_event,
                    "balance": balance.evidence,
                },
            )
        )
        if chain_total >= threshold:
            return FundingChainResult(
                status="qualified",
                links=links,
                chain_total_balance_usd=chain_total,
                chain_depth=depth,
                threshold_usd=threshold,
                first_event=first_event,
                evidence=_evidence(target, chain_total, threshold, links),
            )

        seen_wallets.add(source_wallet)
        current_wallet = source_wallet
        before_time = event.event_time

    return _rejected(
        links=links,
        total=chain_total,
        depth=len(links),
        threshold=threshold,
        reason="insufficient_chain_balance",
        target=target,
    )


def _rejected(
    *,
    links: list[FundingChainLink],
    total: Decimal,
    depth: int,
    threshold: Decimal,
    reason: str,
    target: str,
) -> FundingChainResult:
    return FundingChainResult(
        status="rejected",
        links=links,
        chain_total_balance_usd=total,
        chain_depth=depth,
        threshold_usd=threshold,
        reject_reason=reason,  # type: ignore[arg-type]
        evidence=_evidence(target, total, threshold, links, reason=reason),
    )


def _evidence(
    target: str,
    total: Decimal,
    threshold: Decimal,
    links: list[FundingChainLink],
    *,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "target_address": target,
        "chain_total_balance_usd": str(total),
        "threshold_usd": str(threshold),
        "chain_depth": len(links),
        "reject_reason": reason,
        "links": [
            {
                "depth": link.depth,
                "wallet_address": link.wallet_address,
                "funded_by_address": link.funded_by_address,
                "amount_usdc": str(link.amount_usdc)
                if link.amount_usdc is not None
                else None,
                "event_time": link.event_time.isoformat()
                if link.event_time
                else None,
                "tx_hash": link.tx_hash,
                "balance_usd": str(link.balance_usd)
                if link.balance_usd is not None
                else None,
                "balance_source": link.balance_source,
            }
            for link in links
        ],
    }

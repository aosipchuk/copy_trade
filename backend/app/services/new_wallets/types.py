from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.services.hyperliquid.funding_events import FundingEvent

QualificationStatus = Literal["qualified", "rejected"]

RejectReason = Literal[
    "insufficient_chain_balance",
    "already_trading",
    "missing_funding_source",
    "source_not_wallet",
    "chain_cycle",
    "balance_fetch_failed",
    "provider_unavailable",
]


@dataclass(frozen=True, slots=True)
class FundingChainLink:
    depth: int
    wallet_address: str
    funded_by_address: str | None
    amount_usdc: Decimal | None
    event_time: datetime | None
    tx_hash: str | None
    balance_usd: Decimal | None
    balance_source: str | None
    raw_event_json: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FundingChainResult:
    status: QualificationStatus
    links: list[FundingChainLink]
    chain_total_balance_usd: Decimal
    chain_depth: int
    threshold_usd: Decimal
    reject_reason: RejectReason | None = None
    first_event: FundingEvent | None = None
    evidence: dict[str, Any] | None = None

    @property
    def qualified(self) -> bool:
        return self.status == "qualified"

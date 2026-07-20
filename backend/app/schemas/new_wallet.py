from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.subscription import SizingMode

NewWalletCandidateStatus = Literal[
    "pending",
    "qualified",
    "rejected",
    "subscribed",
    "expired",
    "disabled",
]
UserNewWalletSubscriptionStatus = Literal["active", "paused", "canceled"]
UserNewWalletItemStatus = Literal["active", "expired", "failed", "removed"]


class NewWalletFundingLinkResponse(BaseModel):
    id: int
    depth: int
    wallet_address: str
    funded_by_address: str | None
    amount_usdc: float | None
    event_time: datetime | None
    tx_hash: str | None
    balance_usd: float | None
    balance_source: str | None


class NewWalletCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trader_id: int | None
    hl_address: str
    status: NewWalletCandidateStatus
    detected_at: datetime
    funded_at: datetime | None
    qualified_at: datetime | None
    last_checked_at: datetime | None
    chain_depth: int | None
    chain_total_balance_usd: float | None
    threshold_usd_snapshot: float | None
    reject_reason: str | None
    first_seen_tx_hash: str | None
    links: list[NewWalletFundingLinkResponse] = Field(default_factory=list)
    user_item_status: UserNewWalletItemStatus | None = None
    user_child_subscription_id: int | None = None
    user_child_expires_at: datetime | None = None


class NewWalletCandidateListResponse(BaseModel):
    items: list[NewWalletCandidateResponse]
    next_cursor: str | None


class NewWalletSettingsSnapshot(BaseModel):
    discovery_enabled: bool
    auto_attach_enabled: bool
    funding_provider_configured: bool
    chain_balance_threshold_usd: float
    max_chain_depth: int
    subscription_ttl_days: int
    min_incoming_amount_usd: float
    max_active_per_user: int
    default_max_per_wallet_usd: float


class NewWalletSummaryResponse(BaseModel):
    counts_by_status: dict[str, int]
    active_subscription: "UserNewWalletSubscriptionResponse | None"
    settings: NewWalletSettingsSnapshot


class NewWalletSubscriptionCreate(BaseModel):
    is_demo: bool = True
    total_allocation_usd: float = Field(default=500, gt=10, le=100_000)
    max_active_wallets: int = Field(default=5, ge=1, le=50)
    max_per_wallet_usd: float = Field(default=100, gt=10, le=100_000)
    copy_ratio_pct: float = Field(default=100, ge=10, le=100)
    stop_loss_pct: float = Field(default=20, ge=5, le=50)
    max_leverage: float = Field(default=10, ge=1, le=40)
    sizing_mode: SizingMode = "fixed_ratio"
    allowed_coins: list[str] | None = None
    close_positions_on_expire: bool = True
    risk_disclosure_accepted: bool = False


class UserNewWalletItemResponse(BaseModel):
    id: int
    candidate_id: int
    subscription_id: int
    trader_id: int
    target_allocation_usd: float
    status: UserNewWalletItemStatus
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    error_msg: str | None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_count: int = 0
    candidate: NewWalletCandidateResponse | None = None


class UserNewWalletSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    status: UserNewWalletSubscriptionStatus
    is_demo: bool
    total_allocation_usd: float
    max_active_wallets: int
    max_per_wallet_usd: float
    copy_ratio_pct: float
    stop_loss_pct: float
    max_leverage: float
    sizing_mode: str
    allowed_coins: list[str] | None
    close_positions_on_expire: bool
    created_at: datetime
    updated_at: datetime
    canceled_at: datetime | None
    items: list[UserNewWalletItemResponse] = Field(default_factory=list)


class AdminNewWalletRescanRequest(BaseModel):
    hl_address: str


NewWalletSummaryResponse.model_rebuild()

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SizingMode = Literal["fixed_ratio", "fixed_usd", "equity_pct"]
RiskProfile = Literal["conservative", "balanced", "aggressive"]
ModelPortfolioStatus = Literal["draft", "active", "paused", "retired"]
PortfolioVersionStatus = Literal["draft", "published", "retired", "rejected"]
UserPortfolioStatus = Literal["trialing", "active", "past_due", "paused", "canceled"]
PortfolioItemStatus = Literal["active", "removed", "failed", "paused"]
RebalanceEventType = Literal["scheduled", "emergency", "manual", "user_apply"]
RebalanceStatus = Literal[
    "draft", "pending", "running", "completed", "failed", "skipped"
]
JsonDict = dict[str, Any]


class ModelPortfolioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    risk_profile: RiskProfile
    status: ModelPortfolioStatus
    description: str | None
    methodology_version: str
    rebalance_cadence: str
    min_equity_usd: float
    monthly_price_usd: float
    trial_days: int
    created_at: datetime
    updated_at: datetime


class ModelPortfolioAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    trader_id: int
    target_weight_pct: float = Field(gt=0, le=100)
    copy_ratio_pct: float = Field(gt=0, le=100)
    max_leverage: float
    stop_loss_pct: float
    sizing_mode: SizingMode
    max_per_coin_usd: float | None
    allowed_coins: list[str] | None
    reason_code: str | None
    reason_text: str | None
    score_snapshot: JsonDict | None
    constraint_snapshot: JsonDict | None
    created_at: datetime


class ModelPortfolioVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    version_no: int
    status: PortfolioVersionStatus
    valid_from: datetime | None
    valid_to: datetime | None
    created_by: int | None
    approved_by: int | None
    approved_at: datetime | None
    approval_note: str | None
    selection_started_at: datetime | None
    selection_finished_at: datetime | None
    facts_hash: str | None
    summary_json: JsonDict | None
    created_at: datetime


class ModelPortfolioVersionDetailResponse(ModelPortfolioVersionResponse):
    allocations: list[ModelPortfolioAllocationResponse] = Field(default_factory=list)


class UserPortfolioSubscriptionCreate(BaseModel):
    portfolio_id: int
    active_version_id: int
    is_demo: bool = True
    auto_rebalance: bool = False
    total_allocation_usd: float = Field(gt=10, le=1_000_000)
    close_removed_positions: bool = False


class UserPortfolioSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    portfolio_id: int
    active_version_id: int
    status: UserPortfolioStatus
    is_demo: bool
    auto_rebalance: bool
    total_allocation_usd: float
    close_removed_positions: bool
    billing_provider: str | None
    billing_customer_id: str | None
    billing_subscription_id: str | None
    current_period_end: datetime | None
    created_at: datetime
    updated_at: datetime
    canceled_at: datetime | None


class UserPortfolioItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_portfolio_subscription_id: int
    subscription_id: int
    portfolio_version_id: int
    allocation_id: int
    trader_id: int
    target_allocation_usd: float
    target_weight_pct: float = Field(gt=0, le=100)
    status: PortfolioItemStatus
    created_at: datetime
    removed_at: datetime | None


class PortfolioRebalanceEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_id: int
    from_version_id: int | None
    to_version_id: int | None
    user_portfolio_subscription_id: int | None
    event_type: RebalanceEventType
    status: RebalanceStatus
    diff_json: JsonDict | None
    error_msg: str | None
    idempotency_key: str
    created_at: datetime
    executed_at: datetime | None


class PortfolioBacktestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_version_id: int
    period_days: int
    initial_equity_usd: float
    total_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    win_rate_pct: float | None
    turnover_pct: float | None
    fees_usd: float | None
    slippage_usd: float | None
    missed_trade_count: int
    assumptions_json: JsonDict
    equity_curve_json: JsonDict
    created_at: datetime


class PortfolioCurrentVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_no: int
    status: PortfolioVersionStatus
    valid_from: datetime | None
    approved_at: datetime | None
    trader_count: int
    target_weight_sum_pct: float
    summary_json: JsonDict | None


class PortfolioBacktestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    portfolio_version_id: int
    period_days: int
    initial_equity_usd: float
    total_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    win_rate_pct: float | None
    assumptions_json: JsonDict
    created_at: datetime


class ModelPortfolioListItemResponse(ModelPortfolioResponse):
    current_version: PortfolioCurrentVersionSummary | None
    latest_backtest: PortfolioBacktestSummary | None


class ModelPortfolioAllocationDetailResponse(ModelPortfolioAllocationResponse):
    trader_address: str
    trader_display_name: str | None
    portfolio_score: float | None
    source_metrics: JsonDict | None


class ModelPortfolioPublishedVersionDetailResponse(ModelPortfolioVersionResponse):
    allocations: list[ModelPortfolioAllocationDetailResponse] = Field(
        default_factory=list
    )


class ModelPortfolioDetailResponse(ModelPortfolioResponse):
    current_version: ModelPortfolioPublishedVersionDetailResponse
    backtests: list[PortfolioBacktestResponse] = Field(default_factory=list)

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SizingMode = Literal["fixed_ratio", "fixed_usd", "equity_pct"]


class SubscriptionCreate(BaseModel):
    trader_id: int
    max_allocation_usd: float = Field(gt=10, le=100_000)
    copy_ratio_pct: float = Field(default=100, ge=10, le=100)
    stop_loss_pct: float = Field(default=20, ge=5, le=50)
    max_leverage: float = Field(default=10, ge=1, le=40)
    sizing_mode: SizingMode = "fixed_ratio"
    max_per_coin_usd: float | None = Field(None, gt=0)
    allowed_coins: list[str] | None = None
    is_demo: bool = False


class SubscriptionUpdate(BaseModel):
    max_allocation_usd: float | None = Field(None, gt=10, le=100_000)
    copy_ratio_pct: float | None = Field(None, ge=10, le=100)
    stop_loss_pct: float | None = Field(None, ge=5, le=50)
    max_leverage: float | None = Field(None, ge=1, le=40)
    sizing_mode: SizingMode | None = None
    max_per_coin_usd: float | None = None
    allowed_coins: list[str] | None = None


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trader_id: int
    trader_address: str | None
    trader_name: str | None
    max_allocation_usd: float
    copy_ratio_pct: float
    stop_loss_pct: float
    max_leverage: float
    sizing_mode: str
    max_per_coin_usd: float | None
    allowed_coins: list[str] | None
    source_type: str
    source_id: int | None
    source_version_id: int | None
    managed_by_portfolio: bool
    is_active: bool
    is_demo: bool
    created_at: datetime
    realized_pnl: float
    unrealized_pnl: float = 0.0
    trade_count: int


class DemoOpenPosition(BaseModel):
    subscription_id: int
    trader_name: str | None
    coin: str
    side: str
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float


class DemoPortfolioResponse(BaseModel):
    total_realized_pnl: float
    total_unrealized_pnl: float
    trade_count: int
    win_count: int
    win_rate_pct: float
    open_positions: list[DemoOpenPosition]


class DemoTradeItem(BaseModel):
    id: int
    coin: str
    side: str
    size: float
    price: float
    trade_type: str
    realized_pnl: float | None
    executed_at: datetime


class DemoClosedPositionItem(BaseModel):
    coin: str
    direction: str
    size: float
    entry_price: float
    close_price: float
    realized_pnl: float
    opened_at: datetime
    closed_at: datetime

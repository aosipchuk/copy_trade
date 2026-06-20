import base64
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TraderStatSchema(BaseModel):
    period: str
    pnl_usd: float | None
    roi_pct: float | None
    volume_usd: float | None
    win_rate_pct: float | None = None
    max_drawdown_usd: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    avg_trade_duration_hrs: float | None = None
    first_trade_at: datetime | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    profit_factor: float | None = None
    avg_pnl_per_trade: float | None = None
    max_losing_streak: int | None = None
    profitable_days_pct: float | None = None
    avg_trades_per_day: float | None = None
    daily_pnl_std_dev: float | None = None
    long_ratio_pct: float | None = None
    avg_position_size_usd: float | None = None
    fees_paid_usd: float | None = None
    calmar_ratio: float | None = None
    composite_score: float | None = None
    max_drawdown_duration_days: float | None = None


class TraderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hl_address: str
    display_name: str | None
    stats: list[TraderStatSchema]


class TraderListResponse(BaseModel):
    items: list[TraderListItem]
    next_cursor: str | None


class TraderDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hl_address: str
    display_name: str | None
    is_active: bool
    last_seen_at: datetime | None
    stats: list[TraderStatSchema]


class EquityPoint(BaseModel):
    ts: datetime
    pnl: float
    roi: float = 0.0


class PositionItem(BaseModel):
    coin: str
    side: str
    size: float
    entry_px: float | None
    unrealized_pnl: float
    leverage: int


class FillItem(BaseModel):
    coin: str
    side: str  # "B" = buy, "A" = sell
    px: float
    sz: float
    dir: str
    closed_pnl: float
    time: int  # ms


class ClosedTradeItem(BaseModel):
    coin: str
    direction: str  # "long" | "short"
    size: float
    avg_px: float
    pnl: float
    time: int  # ms, timestamp of first fill in the order
    fill_count: int


class TraderSummaryResponse(BaseModel):
    id: int
    hl_address: str
    display_name: str | None
    stats: dict[str, TraderStatSchema]
    equity_curve_week: list[EquityPoint]
    open_positions: list[PositionItem]
    recent_trades: list[ClosedTradeItem]


def encode_cursor(sort_value: float | None, trader_id: int) -> str:
    data = {"v": sort_value, "id": trader_id}
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> tuple[float | None, int]:
    data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return data["v"], data["id"]

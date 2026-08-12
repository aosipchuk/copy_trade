from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WindowPerf(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pnl: Decimal
    roi: Decimal
    vlm: Decimal


class LeaderboardRow(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    eth_address: str = Field(alias="ethAddress")
    account_value: Decimal = Field(alias="accountValue")
    display_name: str | None = Field(None, alias="displayName")
    # [["day", {...}], ["week", {...}], ...]
    window_performances: list[tuple[str, WindowPerf]] = Field(
        alias="windowPerformances"
    )

    def get_perf(self, period: str) -> WindowPerf | None:
        for p, w in self.window_performances:
            if p == period:
                return w
        return None


class LeaderboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    leaderboard_rows: list[LeaderboardRow] = Field(alias="leaderboardRows")


class PositionLeverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str  # "cross" | "isolated"
    value: int


class Position(BaseModel):
    """Single open perp position from clearinghouseState."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    coin: str
    szi: Decimal  # signed: positive = long, negative = short
    entry_px: Decimal | None = Field(None, alias="entryPx")
    unrealized_pnl: Decimal = Field(alias="unrealizedPnl")
    leverage: PositionLeverage

    @property
    def side(self) -> str:
        return "long" if self.szi > Decimal("0") else "short"

    @property
    def abs_size(self) -> Decimal:
        return abs(self.szi)


class AssetPosition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    position: Position
    type: str


class MarginSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    account_value: Decimal = Field(alias="accountValue")
    total_margin_used: Decimal = Field(alias="totalMarginUsed")
    total_raw_usd: Decimal = Field(alias="totalRawUsd")


class ClearinghouseState(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    asset_positions: list[AssetPosition] = Field(alias="assetPositions")
    margin_summary: MarginSummary | None = Field(None, alias="marginSummary")
    cross_margin_summary: MarginSummary | None = Field(None, alias="crossMarginSummary")
    withdrawable: Decimal | None = None

    @property
    def open_positions(self) -> list[Position]:
        return [
            ap.position
            for ap in self.asset_positions
            if ap.position.szi != Decimal("0")
        ]


class LedgerDelta(BaseModel):
    """Flexible model for userNonFundingLedgerUpdates delta payloads."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str
    usdc: Decimal | None = None
    amount: Decimal | None = None
    usdc_value: Decimal | None = Field(None, alias="usdcValue")
    token: str | None = None
    user: str | None = None
    from_address: str | None = Field(None, alias="from")
    from_user: str | None = Field(None, alias="fromUser")
    source: str | None = None
    source_user: str | None = Field(None, alias="sourceUser")
    sender: str | None = None
    to_address: str | None = Field(None, alias="to")
    to_user: str | None = Field(None, alias="toUser")
    destination: str | None = None

    @property
    def amount_usdc(self) -> Decimal | None:
        if self.usdc is not None:
            return self.usdc
        if self.usdc_value is not None:
            return self.usdc_value
        return self.amount

    @property
    def source_address(self) -> str | None:
        return (
            self.from_address
            or self.user
            or self.from_user
            or self.source
            or self.source_user
            or self.sender
        )


class NonFundingLedgerUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    time: int
    hash: str | None = None
    delta: LedgerDelta


class SpotBalance(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    coin: str
    total: Decimal
    hold: Decimal | None = None
    entry_ntl: Decimal | None = Field(None, alias="entryNtl")


class SpotClearinghouseState(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    balances: list[SpotBalance] = Field(default_factory=list)


class AccountEquitySnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    balance_usd: Decimal
    balance_source: str
    perp_account_value_usd: Decimal
    spot_usdc_total: Decimal
    evidence: dict[str, Any]


class Fill(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    coin: str
    px: Decimal
    sz: Decimal
    side: str  # "B" = buy/long, "A" = ask/sell/short
    time: int  # ms timestamp
    closed_pnl: Decimal = Field(alias="closedPnl")
    dir: str  # "Open Long", "Close Short", etc.
    oid: int
    fee: Decimal = Field(default=Decimal("0"))
    start_position: Decimal | None = Field(None, alias="startPosition")
    hash: str | None = None
    tid: int | None = None


class AssetMeta(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    sz_decimals: int = Field(alias="szDecimals")
    max_leverage: int = Field(alias="maxLeverage")
    is_delisted: bool = Field(False, alias="isDelisted")
    margin_table_id: int | None = Field(None, alias="marginTableId")


class Meta(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    universe: list[AssetMeta]
    collateral_token: int | None = Field(None, alias="collateralToken")

    def asset_index(self, coin: str) -> int | None:
        for i, asset in enumerate(self.universe):
            if asset.name == coin:
                return i
        return None


class AssetContext(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    mark_px: Decimal | None = Field(None, alias="markPx")
    oracle_px: Decimal | None = Field(None, alias="oraclePx")
    mid_px: Decimal | None = Field(None, alias="midPx")
    open_interest: Decimal | None = Field(None, alias="openInterest")


class OpenOrder(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    coin: str
    oid: int
    side: str
    size: Decimal = Field(alias="sz")
    limit_price: Decimal = Field(alias="limitPx")
    reduce_only: bool = Field(False, alias="reduceOnly")
    cloid: str | None = None
    timestamp: int | None = None


class UserAbstraction(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    abstraction: str | None = None
    dex_abstraction_enabled: bool | None = Field(None, alias="dexAbstractionEnabled")

    @property
    def mode(self) -> str:
        if self.abstraction in (None, "disabled"):
            return (
                "legacy_dex_abstraction"
                if self.dex_abstraction_enabled
                else "standard"
            )
        if self.abstraction == "unifiedAccount":
            return "unified"
        if self.abstraction == "portfolioMargin":
            return "portfolio_margin"
        return "unknown"


class SubAccount(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    sub_account_user: str = Field(alias="subAccountUser")
    master: str


class PlacementResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    oid: int | None = None
    filled_size: Decimal = Decimal("0")
    average_price: Decimal | None = None
    error: str | None = None


class OrderStatusResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    oid: int | None = None
    cloid: str | None = None
    status_timestamp: int | None = None


class PerpDex(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str

from decimal import Decimal

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

    @property
    def open_positions(self) -> list[Position]:
        return [
            ap.position
            for ap in self.asset_positions
            if ap.position.szi != Decimal("0")
        ]


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


class AssetMeta(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    sz_decimals: int = Field(alias="szDecimals")
    max_leverage: int = Field(alias="maxLeverage")


class Meta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    universe: list[AssetMeta]

    def asset_index(self, coin: str) -> int | None:
        for i, asset in enumerate(self.universe):
            if asset.name == coin:
                return i
        return None

from pydantic import BaseModel, ConfigDict, Field


class HydromancerUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: str
    human_score: int = Field(alias="humanScore")
    total_pnl: float = Field(alias="totalPnl")
    total_trades: int = Field(alias="totalTrades")
    days_active: int = Field(alias="daysActive")
    volume_traded: float = Field(alias="volumeTraded")


class HydromancerLeaderboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    users: list[HydromancerUser]
    total: int
    limit: int
    offset: int

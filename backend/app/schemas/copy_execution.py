from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CopyAccountOption(BaseModel):
    address: str
    name: str
    kind: Literal["master", "subaccount"]


class CopyAccountSelectionRequest(BaseModel):
    account_address: str
    dedicated_account_acknowledged: bool


class CopyAccountStateResponse(BaseModel):
    master_address: str | None
    account_address: str | None
    vault_address: str | None
    account_mode: str | None
    status: str
    reason: str | None
    dedicated_confirmed_at: datetime | None
    options: list[CopyAccountOption] = Field(default_factory=list)


class PreflightCheck(BaseModel):
    code: str
    ok: bool
    message: str


class BlockingPosition(BaseModel):
    dex: str
    coin: str
    side: str
    size: float


class BlockingOrder(BaseModel):
    dex: str
    coin: str
    side: str
    size: float
    oid: int
    cloid: str | None


class CopyPreflightResponse(BaseModel):
    ok: bool
    account_mode: str | None
    equity_usd: float | None
    checked_at: datetime
    checks: list[PreflightCheck]
    positions: list[BlockingPosition]
    open_orders: list[BlockingOrder]


class ManagedResumeResponse(BaseModel):
    child_count: int
    baseline_market_count: int
    warning: str

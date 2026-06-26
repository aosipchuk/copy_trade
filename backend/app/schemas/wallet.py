import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class WalletSetupResponse(BaseModel):
    agent_address: str
    nonce: int
    eip712_payload: dict[str, Any]


_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class WalletApproveRequest(BaseModel):
    user_address: str | None = None
    nonce: int
    signature: dict[str, Any]  # {"r": "0x...", "s": "0x...", "v": 27}

    @field_validator("user_address")
    @classmethod
    def validate_evm_address(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _EVM_ADDRESS_RE.fullmatch(v):
            raise ValueError(
                "user_address must be a valid EVM address: "
                "0x followed by 40 hex characters."
            )
        return v.lower()


class WalletBalanceResponse(BaseModel):
    account_value: float
    total_margin_used: float
    available: float


class WalletPositionItem(BaseModel):
    coin: str
    side: str
    size: float
    entry_px: float | None
    unrealized_pnl: float
    leverage: int
    subscription_id: int | None = None


class AgentStatusResponse(BaseModel):
    agent_address: str | None
    is_active: bool
    approved_at: str | None
    builder_fee_approved: bool = False


class WalletBuilderSetupResponse(BaseModel):
    nonce: int
    eip712_payload: dict[str, Any]


class WalletBuilderApproveRequest(BaseModel):
    nonce: int
    signature: dict[str, Any]  # {"r": "0x...", "s": "0x...", "v": 27}


class CloseAllResponse(BaseModel):
    closed: int
    subscriptions_paused: int


class ActivityItem(BaseModel):
    action: str
    coin: str | None
    side: str | None
    size: float | None
    pnl: float | None
    ts: datetime
    subscription_trader: str | None


class PortfolioRiskResponse(BaseModel):
    portfolio_stop_loss_pct: float | None


class PortfolioRiskUpdate(BaseModel):
    portfolio_stop_loss_pct: float | None = Field(default=None, ge=1.0, le=100.0)

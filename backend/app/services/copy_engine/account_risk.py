from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskAllocation:
    target_size: Decimal
    price: Decimal
    subscription_max_leverage: Decimal
    market_max_leverage: Decimal


def required_initial_margin(allocations: list[RiskAllocation]) -> Decimal:
    total = Decimal("0")
    for allocation in allocations:
        leverage = min(
            allocation.subscription_max_leverage,
            allocation.market_max_leverage,
        )
        if leverage <= 0:
            raise ValueError("Leverage must be positive")
        total += abs(allocation.target_size) * allocation.price / leverage
    return total


def risk_increase_allowed(
    *,
    before_size: Decimal,
    target_size: Decimal,
    required_margin_usd: Decimal,
    collateral_usd: Decimal | None,
) -> bool:
    if abs(target_size) <= abs(before_size):
        return True
    return collateral_usd is not None and required_margin_usd <= collateral_usd

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.services.copy_engine.constants import IOC_SLIPPAGE, MIN_TRADE_USD
from app.services.copy_engine.target_calculator import round_size_toward_zero


@dataclass(frozen=True)
class OrderParams:
    coin: str
    asset_index: int
    is_buy: bool
    size: Decimal
    limit_px: Decimal
    reduce_only: bool


def _round_limit_price(price: Decimal) -> Decimal:
    if price <= 0:
        raise ValueError("Price must be positive")
    return price.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)


def delta_to_order(
    *,
    coin: str,
    asset_index: int,
    before_size: Decimal,
    target_size: Decimal,
    mid_price: Decimal,
    sz_decimals: int,
) -> OrderParams | None:
    """Convert an aggregate position delta to a safe IOC order."""
    effective_target = target_size
    if before_size and target_size and before_size * target_size < 0:
        effective_target = Decimal("0")
    delta = effective_target - before_size
    size = abs(round_size_toward_zero(delta, sz_decimals))
    if size == 0 or size * mid_price < MIN_TRADE_USD:
        return None

    reducing = before_size != 0 and abs(effective_target) < abs(before_size)
    if reducing:
        size = min(size, abs(before_size))
    is_buy = delta > 0
    multiplier = Decimal("1") + IOC_SLIPPAGE if is_buy else Decimal("1") - IOC_SLIPPAGE
    return OrderParams(
        coin=coin,
        asset_index=asset_index,
        is_buy=is_buy,
        size=size,
        limit_px=_round_limit_price(mid_price * multiplier),
        reduce_only=reducing,
    )


def build_close_order(
    coin: str,
    asset_index: int,
    is_long: bool,
    size: Decimal,
    mid_price: Decimal,
    *,
    sz_decimals: int = 8,
) -> OrderParams:
    order = delta_to_order(
        coin=coin,
        asset_index=asset_index,
        before_size=size if is_long else -size,
        target_size=Decimal("0"),
        mid_price=mid_price,
        sz_decimals=sz_decimals,
    )
    if order is None:
        raise ValueError("Close size is below the executable minimum")
    return order

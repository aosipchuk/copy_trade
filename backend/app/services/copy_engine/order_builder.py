from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.core.logging import get_logger
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.services.copy_engine.constants import (
    COIN_WHITELIST,
    IOC_SLIPPAGE,
    MIN_TRADE_USD,
)
from app.services.hyperliquid.models import AssetMeta

logger = get_logger(__name__)


@dataclass(frozen=True)
class OrderParams:
    coin: str
    asset_index: int
    is_buy: bool
    size: Decimal
    limit_px: Decimal
    reduce_only: bool = False


def signal_to_order(
    signal: Signal,
    subscription: Subscription,
    user_equity: Decimal,
    mid_price: Decimal,
    asset_meta: AssetMeta,
) -> OrderParams | None:
    """
    Translate a signal + subscription settings into executable order params.
    Returns None if the trade should be skipped (below min size, not whitelisted, etc.).
    """
    coin = signal.coin

    # Per-subscription coin filter takes priority over global whitelist
    allowed = getattr(subscription, "allowed_coins", None)
    if allowed is not None and coin not in allowed:
        logger.debug("order_builder_coin_not_in_allowed_list", coin=coin)
        return None

    if coin not in COIN_WHITELIST:
        logger.debug("order_builder_coin_not_whitelisted", coin=coin)
        return None

    if mid_price <= Decimal("0"):
        logger.warning("order_builder_invalid_mid_price", coin=coin, mid=str(mid_price))
        return None

    max_alloc = Decimal(str(subscription.max_allocation_usd))
    sizing_mode: str = getattr(subscription, "sizing_mode", None) or "fixed_ratio"

    if sizing_mode == "fixed_usd":
        # Fixed dollar amount per trade, capped by max_allocation
        notional = max_alloc
    elif sizing_mode == "equity_pct":
        # Percentage of user's current equity on HL
        copy_ratio = Decimal(str(subscription.copy_ratio_pct)) / 100
        notional = min(max_alloc, user_equity * copy_ratio)
    else:
        # fixed_ratio: copy_ratio_pct% of trader's current position size
        if signal.size is None:
            logger.debug("order_builder_no_trader_size_for_fixed_ratio", coin=coin)
            return None
        copy_ratio = Decimal(str(subscription.copy_ratio_pct)) / 100
        trader_notional = Decimal(str(signal.size)) * mid_price
        notional = min(max_alloc, trader_notional * copy_ratio)

    # Cap per-coin exposure
    max_per_coin = getattr(subscription, "max_per_coin_usd", None)
    if max_per_coin is not None:
        notional = min(notional, Decimal(str(max_per_coin)))

    # Cap by max_leverage: notional must not exceed equity × max_leverage
    if user_equity > Decimal("0"):
        max_lev = Decimal(str(subscription.max_leverage))
        notional = min(notional, user_equity * max_lev)

    if notional < MIN_TRADE_USD:
        logger.debug("order_builder_below_min", coin=coin, notional=str(notional))
        return None

    # Convert USD notional to contract size
    quantize = Decimal("10") ** (-asset_meta.sz_decimals)
    size = (notional / mid_price).quantize(quantize, rounding=ROUND_DOWN)

    if size <= Decimal("0"):
        logger.debug("order_builder_zero_size", coin=coin)
        return None

    # Verify notional still meets minimum after rounding
    if size * mid_price < MIN_TRADE_USD:
        logger.debug("order_builder_rounded_below_min", coin=coin, size=str(size))
        return None

    # Determine order direction
    side = signal.side or "long"
    is_buy = side == "long"

    # IOC limit price with slippage tolerance
    if is_buy:
        limit_px = mid_price * (1 + IOC_SLIPPAGE)
    else:
        limit_px = mid_price * (1 - IOC_SLIPPAGE)

    # Round price to 5 significant figures (HL convention)
    limit_px = limit_px.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

    return OrderParams(
        coin=coin,
        asset_index=0,  # placeholder — caller must set correct index
        is_buy=is_buy,
        size=size,
        limit_px=limit_px,
    )


def build_close_order(
    coin: str,
    asset_index: int,
    is_long: bool,
    size: Decimal,
    mid_price: Decimal,
) -> OrderParams:
    """Build a reduce-only IOC order to close an existing position."""
    if is_long:
        limit_px = mid_price * (1 - IOC_SLIPPAGE)
    else:
        limit_px = mid_price * (1 + IOC_SLIPPAGE)
    limit_px = limit_px.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

    return OrderParams(
        coin=coin,
        asset_index=asset_index,
        is_buy=not is_long,
        size=size,
        limit_px=limit_px,
        reduce_only=True,
    )

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True)
class TargetInput:
    dex: str
    coin: str
    leader_size: Decimal
    price: Decimal
    sz_decimals: int


@dataclass(frozen=True)
class TargetResult:
    dex: str
    coin: str
    leader_size: Decimal
    raw_target_size: Decimal
    target_size: Decimal
    target_notional_usd: Decimal
    price: Decimal


def round_size_toward_zero(size: Decimal, sz_decimals: int) -> Decimal:
    quantum = Decimal("1").scaleb(-sz_decimals)
    magnitude = abs(size).quantize(quantum, rounding=ROUND_DOWN)
    return magnitude if size >= 0 else -magnitude


def _raw_target(
    item: TargetInput,
    *,
    sizing_mode: str,
    copy_ratio_pct: Decimal,
    max_allocation_usd: Decimal,
    equity_usd: Decimal | None,
) -> Decimal:
    if item.leader_size == 0:
        return Decimal("0")
    direction = Decimal("1") if item.leader_size > 0 else Decimal("-1")
    if sizing_mode == "fixed_ratio":
        return item.leader_size * copy_ratio_pct / Decimal("100")
    if sizing_mode == "fixed_usd":
        return direction * max_allocation_usd / item.price
    if sizing_mode == "equity_pct":
        if equity_usd is None or equity_usd < 0:
            raise ValueError("Fresh copy-account equity is required")
        notional = equity_usd * copy_ratio_pct / Decimal("100")
        return direction * notional / item.price
    raise ValueError(f"Unsupported sizing mode: {sizing_mode}")


def calculate_targets(
    items: list[TargetInput],
    *,
    sizing_mode: str,
    copy_ratio_pct: Decimal,
    max_allocation_usd: Decimal,
    max_per_coin_usd: Decimal | None,
    equity_usd: Decimal | None,
) -> list[TargetResult]:
    """Calculate final signed targets and cap final exposure, not order deltas."""
    raw_by_market: list[tuple[TargetInput, Decimal]] = []
    for item in items:
        if item.price <= 0:
            raise ValueError(f"Invalid price for {item.dex}:{item.coin}")
        raw = _raw_target(
            item,
            sizing_mode=sizing_mode,
            copy_ratio_pct=copy_ratio_pct,
            max_allocation_usd=max_allocation_usd,
            equity_usd=equity_usd,
        )
        if max_per_coin_usd is not None:
            max_size = max_per_coin_usd / item.price
            raw = max(-max_size, min(max_size, raw))
        raw_by_market.append((item, raw))

    total_notional = sum(
        (abs(raw) * item.price for item, raw in raw_by_market),
        start=Decimal("0"),
    )
    scale = (
        max_allocation_usd / total_notional
        if total_notional > max_allocation_usd and total_notional > 0
        else Decimal("1")
    )

    results: list[TargetResult] = []
    for item, raw in raw_by_market:
        target = round_size_toward_zero(raw * scale, item.sz_decimals)
        results.append(
            TargetResult(
                dex=item.dex,
                coin=item.coin,
                leader_size=item.leader_size,
                raw_target_size=raw,
                target_size=target,
                target_notional_usd=abs(target) * item.price,
                price=item.price,
            )
        )
    return results

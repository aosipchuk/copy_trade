from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from app.services.portfolio.correlation import max_correlation_to_selected
from app.services.portfolio.types import (
    OptimizationResult,
    OptimizedAllocation,
    RejectedCandidate,
    RiskProfileConfig,
    ScoredCandidate,
)


class PortfolioOptimizationError(ValueError):
    pass


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _raw_weight(candidate: ScoredCandidate) -> Decimal:
    drawdown = max(candidate.candidate.metrics.max_drawdown_pct or 0.0, 5.0)
    return _to_decimal(max(candidate.portfolio_score, 0.01) / drawdown)


def _cap_and_normalize_weights(
    selected: Sequence[ScoredCandidate], max_weight_pct: float
) -> dict[int, float]:
    if not selected:
        return {}

    cap = _to_decimal(max_weight_pct)
    if Decimal(len(selected)) * cap < Decimal("100"):
        raise PortfolioOptimizationError(
            "Cannot normalize weights to 100% with the configured max weight cap."
        )

    raw_weights = {
        candidate.candidate.trader_id: _raw_weight(candidate) for candidate in selected
    }
    if sum(raw_weights.values(), Decimal("0")) <= Decimal("0"):
        raw_weights = {
            candidate.candidate.trader_id: Decimal("1") for candidate in selected
        }

    remaining = Decimal("100")
    uncapped = set(raw_weights)
    weights: dict[int, Decimal] = {}

    while uncapped:
        total_raw = sum(
            (raw_weights[trader_id] for trader_id in uncapped), Decimal("0")
        )
        changed = False
        for trader_id in sorted(uncapped):
            proposed = remaining * raw_weights[trader_id] / total_raw
            if proposed > cap:
                weights[trader_id] = cap
                remaining -= cap
                uncapped.remove(trader_id)
                changed = True
        if not changed:
            for trader_id in sorted(uncapped):
                weights[trader_id] = remaining * raw_weights[trader_id] / total_raw
            break

    rounded = {
        trader_id: weight.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        for trader_id, weight in weights.items()
    }
    residual = Decimal("100.000") - sum(rounded.values(), Decimal("0"))
    if residual != Decimal("0.000"):
        if residual > 0:
            ordered_ids = sorted(
                raw_weights, key=lambda item: raw_weights[item], reverse=True
            )
        else:
            ordered_ids = sorted(rounded, key=lambda item: rounded[item], reverse=True)
        for trader_id in ordered_ids:
            next_weight = rounded[trader_id] + residual
            if Decimal("0.001") <= next_weight <= cap:
                rounded[trader_id] = next_weight
                break

    return {trader_id: float(weight) for trader_id, weight in rounded.items()}


def optimize_portfolio(
    candidates: Sequence[ScoredCandidate],
    config: RiskProfileConfig,
) -> OptimizationResult:
    ordered_candidates = sorted(
        candidates,
        key=lambda item: (-item.portfolio_score, item.candidate.trader_id),
    )
    selected: list[ScoredCandidate] = []
    rejected: list[RejectedCandidate] = []
    correlation_snapshots: dict[int, dict[str, object]] = {}

    for candidate in ordered_candidates:
        if len(selected) >= config.max_traders:
            break

        correlation = max_correlation_to_selected(candidate, selected)
        correlation_snapshot: dict[str, object] = {
            "status": correlation.status,
            "max_abs_correlation": correlation.max_abs_correlation,
            "peer_trader_id": correlation.peer_trader_id,
            "overlapping_days": correlation.overlapping_days,
            "max_correlation": config.max_correlation,
        }
        if (
            correlation.max_abs_correlation is not None
            and correlation.max_abs_correlation > config.max_correlation
        ):
            rejected.append(
                RejectedCandidate(
                    trader_id=candidate.candidate.trader_id,
                    reason_code="high_correlation",
                    reason_text=(
                        "Trader correlation with the selected portfolio exceeds "
                        "the diversification limit."
                    ),
                    constraint_snapshot=correlation_snapshot,
                )
            )
            continue

        correlation_snapshots[candidate.candidate.trader_id] = correlation_snapshot
        selected.append(candidate)

    if len(selected) < config.min_traders:
        raise PortfolioOptimizationError(
            f"Need at least {config.min_traders} eligible traders, got {len(selected)}."
        )

    weights = _cap_and_normalize_weights(selected, config.max_weight_pct)
    allocations: list[OptimizedAllocation] = []
    for index, candidate in enumerate(selected, start=1):
        trader_id = candidate.candidate.trader_id
        metrics = candidate.candidate.metrics
        avg_leverage_text = (
            f"{metrics.avg_leverage:.2f}x"
            if metrics.avg_leverage is not None
            else "unknown"
        )
        allocations.append(
            OptimizedAllocation(
                scored_candidate=candidate,
                target_weight_pct=weights[trader_id],
                copy_ratio_pct=100.0,
                max_leverage=config.max_leverage,
                stop_loss_pct=config.default_stop_loss_pct,
                sizing_mode="fixed_ratio",
                max_per_coin_usd=None,
                allowed_coins=None,
                reason_code="balanced_mvp_score",
                reason_text=(
                    "Selected by deterministic Balanced MVP methodology: "
                    f"portfolio_score={candidate.portfolio_score:.2f}, "
                    f"drawdown={metrics.max_drawdown_pct:.2f}%, "
                    f"avg_leverage={avg_leverage_text}."
                ),
                constraint_snapshot={
                    **candidate.candidate.constraint_snapshot,
                    "selection_rank": index,
                    "max_weight_pct": config.max_weight_pct,
                    "target_weight_pct": weights[trader_id],
                    "correlation": correlation_snapshots[trader_id],
                },
            )
        )

    total_weight = round(sum(item.target_weight_pct for item in allocations), 3)
    if total_weight != 100.0:
        raise PortfolioOptimizationError(f"Optimized weights sum to {total_weight}%.")

    summary = {
        "risk_profile": config.risk_profile,
        "trader_count": len(allocations),
        "target_weight_sum_pct": total_weight,
        "max_weight_pct": config.max_weight_pct,
        "max_correlation": config.max_correlation,
        "rejected_count": len(rejected),
    }
    return OptimizationResult(tuple(allocations), tuple(rejected), summary)

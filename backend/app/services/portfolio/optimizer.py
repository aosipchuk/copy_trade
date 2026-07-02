from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.portfolio.advanced import (
    build_account_size_profiles,
    build_exposure_heatmap,
)
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


def _validate_weight_cap(
    selected: Sequence[ScoredCandidate], max_weight_pct: float
) -> None:
    cap = _to_decimal(max_weight_pct)
    if Decimal(len(selected)) * cap < Decimal("100"):
        raise PortfolioOptimizationError(
            "Cannot normalize weights to 100% with the configured max weight cap."
        )


def _heuristic_weight_decimals(
    selected: Sequence[ScoredCandidate], max_weight_pct: float
) -> dict[int, Decimal]:
    if not selected:
        return {}

    cap = _to_decimal(max_weight_pct)
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

    return weights


def _round_weights(
    weights: dict[int, Decimal],
    max_weight_pct: float,
    *,
    residual_order: Sequence[int],
) -> dict[int, float]:
    cap = _to_decimal(max_weight_pct)
    rounded = {
        trader_id: weight.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        for trader_id, weight in weights.items()
    }
    residual = Decimal("100.000") - sum(rounded.values(), Decimal("0"))
    if residual != Decimal("0.000"):
        ordered_ids = list(residual_order)
        if residual < 0:
            ordered_ids = sorted(rounded, key=lambda item: rounded[item], reverse=True)
        for trader_id in ordered_ids:
            next_weight = rounded[trader_id] + residual
            if Decimal("0.001") <= next_weight <= cap:
                rounded[trader_id] = next_weight
                break

    return {trader_id: float(weight) for trader_id, weight in rounded.items()}


def _cap_and_normalize_weights(
    selected: Sequence[ScoredCandidate], max_weight_pct: float
) -> dict[int, float]:
    _validate_weight_cap(selected, max_weight_pct)
    raw_weights = {
        candidate.candidate.trader_id: _raw_weight(candidate) for candidate in selected
    }
    ordered_ids = sorted(raw_weights, key=lambda item: raw_weights[item], reverse=True)
    weights = _heuristic_weight_decimals(selected, max_weight_pct)
    return _round_weights(weights, max_weight_pct, residual_order=ordered_ids)


def _risk_intensity(candidate: ScoredCandidate) -> float:
    metrics = candidate.candidate.metrics
    drawdown = min((metrics.max_drawdown_pct or 0.0) / 50.0, 2.0)
    leverage = min((metrics.avg_leverage or 0.0) / 15.0, 2.0)
    volatility = min((metrics.daily_pnl_std_dev or 0.0) / 2_500.0, 2.0)
    anomaly_snapshot = candidate.score_snapshot.get("anomaly_detection")
    anomaly = (
        float(anomaly_snapshot.get("penalty", 0.0))
        if isinstance(anomaly_snapshot, dict)
        else 0.0
    )
    return max(
        0.1,
        0.35 * drawdown + 0.30 * leverage + 0.20 * volatility + 0.15 * anomaly / 35.0,
    )


def _scipy_optimized_weight_decimals(
    selected: Sequence[ScoredCandidate],
    max_weight_pct: float,
    target_weights: dict[int, float],
) -> tuple[dict[int, Decimal], dict[str, object]] | None:
    try:
        from scipy.optimize import minimize as scipy_minimize
    except Exception:
        return None

    minimize: Any = scipy_minimize
    trader_ids = [candidate.candidate.trader_id for candidate in selected]
    targets = [target_weights[trader_id] for trader_id in trader_ids]
    risk_intensities = [_risk_intensity(candidate) for candidate in selected]

    def objective(weights: Sequence[float]) -> float:
        deviation = sum(
            ((weight - target) / 100.0) ** 2
            for weight, target in zip(weights, targets, strict=True)
        )
        concentration = sum((weight / 100.0) ** 2 for weight in weights)
        risk_weighted = sum(
            ((weight / 100.0) ** 2) * risk
            for weight, risk in zip(weights, risk_intensities, strict=True)
        )
        return deviation + 0.35 * concentration + 0.25 * risk_weighted

    result = minimize(
        objective,
        targets,
        method="SLSQP",
        bounds=[(0.001, max_weight_pct)] * len(selected),
        constraints=({"type": "eq", "fun": lambda weights: sum(weights) - 100.0},),
        options={"maxiter": 200, "ftol": 1e-9, "disp": False},
    )
    if not bool(result.success):
        return None

    raw_weights = {
        trader_id: Decimal(str(float(result.x[index])))
        for index, trader_id in enumerate(trader_ids)
    }
    return raw_weights, {
        "optimizer_engine": "scipy_slsqp",
        "scipy_available": True,
        "objective_value": round(float(result.fun), 8),
    }


def _optimize_weights(
    selected: Sequence[ScoredCandidate], max_weight_pct: float
) -> tuple[dict[int, float], dict[str, object]]:
    _validate_weight_cap(selected, max_weight_pct)
    heuristic_weights = _cap_and_normalize_weights(selected, max_weight_pct)
    raw_weights = {
        candidate.candidate.trader_id: _raw_weight(candidate) for candidate in selected
    }
    residual_order = sorted(
        raw_weights,
        key=lambda item: raw_weights[item],
        reverse=True,
    )
    scipy_result = _scipy_optimized_weight_decimals(
        selected,
        max_weight_pct,
        heuristic_weights,
    )
    if scipy_result is None:
        return heuristic_weights, {
            "optimizer_engine": "heuristic_cap_normalize",
            "scipy_available": False,
            "objective_value": None,
        }

    scipy_weights, summary = scipy_result
    rounded = _round_weights(
        scipy_weights,
        max_weight_pct,
        residual_order=residual_order,
    )
    total_weight = round(sum(rounded.values()), 3)
    if total_weight != 100.0 or max(rounded.values()) > max_weight_pct:
        return heuristic_weights, {
            "optimizer_engine": "heuristic_cap_normalize",
            "scipy_available": True,
            "objective_value": None,
            "fallback_reason": "scipy_result_failed_weight_validation",
        }
    return rounded, summary


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

    weights, weight_optimization_summary = _optimize_weights(
        selected, config.max_weight_pct
    )
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
                reason_code="balanced_advanced_score",
                reason_text=(
                    "Selected by deterministic Balanced advanced methodology: "
                    f"portfolio_score={candidate.portfolio_score:.2f}, "
                    f"drawdown={metrics.max_drawdown_pct:.2f}%, "
                    f"avg_leverage={avg_leverage_text}."
                ),
                constraint_snapshot={
                    **candidate.candidate.constraint_snapshot,
                    "selection_rank": index,
                    "max_weight_pct": config.max_weight_pct,
                    "target_weight_pct": weights[trader_id],
                    "weight_optimization": weight_optimization_summary,
                    "strategy_profile": candidate.score_snapshot.get(
                        "strategy_profile"
                    ),
                    "anomaly_detection": candidate.score_snapshot.get(
                        "anomaly_detection"
                    ),
                    "correlation": correlation_snapshots[trader_id],
                },
            )
        )

    total_weight = round(sum(item.target_weight_pct for item in allocations), 3)
    if total_weight != 100.0:
        raise PortfolioOptimizationError(f"Optimized weights sum to {total_weight}%.")

    account_size_profiles = build_account_size_profiles(allocations)
    exposure_heatmap = build_exposure_heatmap(allocations)
    summary = {
        "risk_profile": config.risk_profile,
        "trader_count": len(allocations),
        "target_weight_sum_pct": total_weight,
        "max_weight_pct": config.max_weight_pct,
        "max_correlation": config.max_correlation,
        "rejected_count": len(rejected),
        "advanced_optimization": {
            **weight_optimization_summary,
            "strategy_clustering": "deterministic_metric_buckets",
            "anomaly_detection": "score_penalty_flags",
            "account_size_profiles": [
                profile["tier"] for profile in account_size_profiles
            ],
            "performance_fee": "deferred_legal_and_billing_review",
        },
        "exposure_heatmap": exposure_heatmap,
        "account_size_profiles": account_size_profiles,
    }
    return OptimizationResult(tuple(allocations), tuple(rejected), summary)

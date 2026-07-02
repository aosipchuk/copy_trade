from collections.abc import Sequence
from typing import Any

from app.services.portfolio.types import CandidateMetrics, OptimizedAllocation

JsonDict = dict[str, Any]

ACCOUNT_SIZE_TIERS: tuple[tuple[str, float], ...] = (
    ("starter", 1_000.0),
    ("standard", 5_000.0),
    ("larger", 10_000.0),
)

MIN_COPYABLE_ALLOCATION_USD = 50.0
MIN_CORRELATION_HISTORY_DAYS = 10


def _safe_float(value: float | int | None, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def _bucket_summary(items: list[tuple[str, float, int]]) -> list[JsonDict]:
    buckets: dict[str, dict[str, float | int]] = {}
    trader_ids_by_bucket: dict[str, set[int]] = {}
    for bucket, weight, trader_id in items:
        current = buckets.setdefault(bucket, {"weight_pct": 0.0, "trader_count": 0})
        current["weight_pct"] = float(current["weight_pct"]) + weight
        trader_ids_by_bucket.setdefault(bucket, set()).add(trader_id)

    for bucket, trader_ids in trader_ids_by_bucket.items():
        buckets[bucket]["trader_count"] = len(trader_ids)

    return [
        {
            "bucket": bucket,
            "weight_pct": round(float(values["weight_pct"]), 3),
            "trader_count": int(values["trader_count"]),
        }
        for bucket, values in sorted(
            buckets.items(),
            key=lambda item: (-float(item[1]["weight_pct"]), item[0]),
        )
    ]


def classify_strategy_profile(metrics: CandidateMetrics) -> JsonDict:
    avg_trades_per_day = metrics.avg_trades_per_day
    avg_trade_duration_hrs = metrics.avg_trade_duration_hrs
    long_ratio_pct = metrics.long_ratio_pct
    profit_factor = metrics.profit_factor
    win_rate_pct = metrics.win_rate_pct
    active_days = metrics.active_trading_days or 0
    trade_count = metrics.trade_count or 0

    if long_ratio_pct is None:
        directional_bias = "unknown"
    elif long_ratio_pct >= 65.0:
        directional_bias = "long_biased"
    elif long_ratio_pct <= 35.0:
        directional_bias = "short_biased"
    else:
        directional_bias = "balanced"

    if metrics.avg_leverage is None:
        leverage_band = "unknown"
    elif metrics.avg_leverage < 2.0:
        leverage_band = "low"
    elif metrics.avg_leverage < 5.0:
        leverage_band = "moderate"
    elif metrics.avg_leverage < 8.0:
        leverage_band = "elevated"
    else:
        leverage_band = "high"

    if metrics.max_drawdown_pct is None:
        risk_band = "unknown"
    elif metrics.max_drawdown_pct <= 12.0:
        risk_band = "low"
    elif metrics.max_drawdown_pct <= 25.0:
        risk_band = "medium"
    else:
        risk_band = "high"

    if (avg_trades_per_day is not None and avg_trades_per_day >= 12.0) or (
        avg_trade_duration_hrs is not None and avg_trade_duration_hrs < 2.0
    ):
        strategy_bucket = "high_frequency"
    elif avg_trade_duration_hrs is not None and avg_trade_duration_hrs >= 24.0:
        strategy_bucket = "position"
    elif directional_bias in {"long_biased", "short_biased"}:
        strategy_bucket = "directional"
    elif (
        profit_factor is not None
        and profit_factor >= 1.8
        and win_rate_pct is not None
        and win_rate_pct >= 58.0
    ):
        strategy_bucket = "consistent_alpha"
    else:
        strategy_bucket = "mixed"

    if active_days >= 90 and trade_count >= 50:
        confidence = "high"
    elif active_days >= 30 and trade_count >= 20:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "strategy_bucket": strategy_bucket,
        "directional_bias": directional_bias,
        "leverage_band": leverage_band,
        "risk_band": risk_band,
        "confidence": confidence,
        "inputs": {
            "avg_trades_per_day": avg_trades_per_day,
            "avg_trade_duration_hrs": avg_trade_duration_hrs,
            "long_ratio_pct": long_ratio_pct,
            "profit_factor": profit_factor,
            "win_rate_pct": win_rate_pct,
            "active_trading_days": metrics.active_trading_days,
            "trade_count": metrics.trade_count,
        },
    }


def detect_candidate_anomalies(metrics: CandidateMetrics) -> JsonDict:
    flags: list[JsonDict] = []

    def add(
        code: str,
        severity: str,
        penalty: float,
        message: str,
        observed: JsonDict,
    ) -> None:
        flags.append(
            {
                "code": code,
                "severity": severity,
                "penalty": penalty,
                "message": message,
                "observed": observed,
            }
        )

    active_days = metrics.active_trading_days or 0
    if metrics.roi_pct is not None and metrics.roi_pct >= 100.0 and active_days < 60:
        add(
            "short_history_high_roi",
            "medium",
            8.0,
            "High ROI is based on a short observed history.",
            {"roi_pct": metrics.roi_pct, "active_trading_days": active_days},
        )

    if metrics.avg_leverage is not None:
        if metrics.avg_leverage >= 10.0:
            add(
                "extreme_leverage",
                "high",
                10.0,
                "Average leverage is high for a model portfolio allocation.",
                {"avg_leverage": metrics.avg_leverage},
            )
        elif metrics.avg_leverage >= 6.0:
            add(
                "elevated_leverage",
                "medium",
                4.0,
                "Average leverage is close to the Balanced risk boundary.",
                {"avg_leverage": metrics.avg_leverage},
            )

    if metrics.max_drawdown_pct is not None and metrics.max_drawdown_pct >= 30.0:
        add(
            "drawdown_near_limit",
            "medium",
            6.0,
            "Max drawdown is near the Balanced hard limit.",
            {"max_drawdown_pct": metrics.max_drawdown_pct},
        )

    if metrics.avg_trades_per_day is not None:
        if metrics.avg_trades_per_day >= 20.0:
            add(
                "copy_frequency_limit",
                "high",
                8.0,
                "Trade frequency is high enough to make copying fragile.",
                {"avg_trades_per_day": metrics.avg_trades_per_day},
            )
        elif metrics.avg_trades_per_day >= 12.0:
            add(
                "elevated_trade_frequency",
                "medium",
                4.0,
                "Trade frequency may increase missed fills and slippage.",
                {"avg_trades_per_day": metrics.avg_trades_per_day},
            )

    if (
        metrics.avg_position_size_usd is not None
        and metrics.avg_position_size_usd < 250.0
    ):
        add(
            "small_position_size",
            "low",
            3.0,
            "Average position size may be hard to copy on small accounts.",
            {"avg_position_size_usd": metrics.avg_position_size_usd},
        )

    history_days = len(metrics.daily_pnl_by_day or {})
    if history_days < MIN_CORRELATION_HISTORY_DAYS:
        add(
            "limited_correlation_history",
            "low",
            3.0,
            "Daily PnL history is limited, so correlation confidence is lower.",
            {"daily_pnl_days": history_days},
        )

    pnl_abs = abs(_safe_float(metrics.pnl_usd))
    if (
        pnl_abs > 0
        and metrics.fees_paid_usd is not None
        and metrics.fees_paid_usd / pnl_abs >= 0.25
    ):
        add(
            "high_fee_drag",
            "medium",
            5.0,
            "Fees are high relative to recorded PnL.",
            {"fees_paid_usd": metrics.fees_paid_usd, "pnl_usd": metrics.pnl_usd},
        )

    penalty = round(min(sum(float(flag["penalty"]) for flag in flags), 35.0), 4)
    if any(flag["severity"] == "high" for flag in flags):
        severity = "high"
    elif any(flag["severity"] == "medium" for flag in flags):
        severity = "medium"
    elif flags:
        severity = "low"
    else:
        severity = "none"

    return {
        "severity": severity,
        "penalty": penalty,
        "flags": flags,
    }


def build_account_size_profiles(
    allocations: Sequence[OptimizedAllocation],
    tiers: Sequence[tuple[str, float]] = ACCOUNT_SIZE_TIERS,
) -> list[JsonDict]:
    profiles: list[JsonDict] = []
    for tier_name, total_allocation_usd in tiers:
        allocation_rows: list[JsonDict] = []
        low_allocation_count = 0
        for allocation in allocations:
            trader_id = allocation.scored_candidate.candidate.trader_id
            allocation_usd = total_allocation_usd * allocation.target_weight_pct / 100.0
            copyability_warning = (
                "below_min_copyable_allocation"
                if allocation_usd < MIN_COPYABLE_ALLOCATION_USD
                else None
            )
            if copyability_warning is not None:
                low_allocation_count += 1
            allocation_rows.append(
                {
                    "trader_id": trader_id,
                    "target_weight_pct": allocation.target_weight_pct,
                    "allocation_usd": round(allocation_usd, 2),
                    "copyability_warning": copyability_warning,
                }
            )

        allocation_values = [row["allocation_usd"] for row in allocation_rows]
        profiles.append(
            {
                "tier": tier_name,
                "total_allocation_usd": total_allocation_usd,
                "min_trader_allocation_usd": (
                    min(allocation_values) if allocation_values else 0.0
                ),
                "max_trader_allocation_usd": (
                    max(allocation_values) if allocation_values else 0.0
                ),
                "low_allocation_trader_count": low_allocation_count,
                "minimum_copyable_allocation_usd": MIN_COPYABLE_ALLOCATION_USD,
                "allocations": allocation_rows,
            }
        )
    return profiles


def build_exposure_heatmap(
    allocations: Sequence[OptimizedAllocation],
) -> JsonDict:
    by_strategy: list[tuple[str, float, int]] = []
    by_direction: list[tuple[str, float, int]] = []
    by_leverage: list[tuple[str, float, int]] = []
    by_risk: list[tuple[str, float, int]] = []

    for allocation in allocations:
        trader_id = allocation.scored_candidate.candidate.trader_id
        strategy_profile = allocation.scored_candidate.score_snapshot.get(
            "strategy_profile", {}
        )
        if not isinstance(strategy_profile, dict):
            strategy_profile = {}
        weight = allocation.target_weight_pct
        by_strategy.append(
            (str(strategy_profile.get("strategy_bucket", "unknown")), weight, trader_id)
        )
        by_direction.append(
            (
                str(strategy_profile.get("directional_bias", "unknown")),
                weight,
                trader_id,
            )
        )
        by_leverage.append(
            (str(strategy_profile.get("leverage_band", "unknown")), weight, trader_id)
        )
        by_risk.append(
            (str(strategy_profile.get("risk_band", "unknown")), weight, trader_id)
        )

    return {
        "by_strategy_bucket": _bucket_summary(by_strategy),
        "by_directional_bias": _bucket_summary(by_direction),
        "by_leverage_band": _bucket_summary(by_leverage),
        "by_risk_band": _bucket_summary(by_risk),
    }

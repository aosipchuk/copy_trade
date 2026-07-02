from collections.abc import Iterable

from app.services.portfolio.advanced import (
    classify_strategy_profile,
    detect_candidate_anomalies,
)
from app.services.portfolio.types import (
    CandidateMetrics,
    PortfolioCandidate,
    ScoredCandidate,
)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _linear(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 50.0
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def _inverse_linear(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 50.0
    return 100.0 - _linear(value, low, high)


def _risk_adjusted_score(metrics: CandidateMetrics) -> float:
    drawdown_score = _inverse_linear(metrics.max_drawdown_pct, 0.0, 35.0)
    sharpe_score = _linear(metrics.sharpe_ratio, 0.0, 3.0)
    sortino_score = _linear(metrics.sortino_ratio, 0.0, 4.0)
    leverage_score = _inverse_linear(metrics.avg_leverage, 1.0, 8.0)
    composite = metrics.composite_score if metrics.composite_score is not None else 50.0
    return _clamp(
        0.30 * drawdown_score
        + 0.25 * sharpe_score
        + 0.20 * sortino_score
        + 0.15 * leverage_score
        + 0.10 * composite
    )


def _consistency_score(metrics: CandidateMetrics) -> float:
    profit_factor_score = _linear(metrics.profit_factor, 1.0, 2.5)
    profitable_days_score = _linear(metrics.profitable_days_pct, 45.0, 75.0)
    calmar_score = _linear(metrics.calmar_ratio, 0.0, 3.0)
    losing_streak_score = _inverse_linear(
        (
            float(metrics.max_losing_streak)
            if metrics.max_losing_streak is not None
            else None
        ),
        0.0,
        8.0,
    )
    drawdown_duration_score = _inverse_linear(
        metrics.max_drawdown_duration_days, 0.0, 45.0
    )
    return _clamp(
        0.30 * profit_factor_score
        + 0.25 * profitable_days_score
        + 0.20 * calmar_score
        + 0.15 * losing_streak_score
        + 0.10 * drawdown_duration_score
    )


def _return_score(metrics: CandidateMetrics) -> float:
    roi_score = _linear(metrics.roi_pct, 0.0, 50.0)
    pnl_per_trade_score = _linear(metrics.avg_pnl_per_trade, 0.0, 150.0)
    win_rate_score = _linear(metrics.win_rate_pct, 40.0, 70.0)
    return _clamp(0.50 * roi_score + 0.25 * pnl_per_trade_score + 0.25 * win_rate_score)


def _copyability_score(metrics: CandidateMetrics) -> float:
    size_score = _linear(metrics.avg_position_size_usd, 500.0, 10_000.0)
    holding_time_score = _linear(metrics.avg_trade_duration_hrs, 1.0, 24.0)
    liquidity_proxy_score = _linear(metrics.volume_usd, 25_000.0, 2_000_000.0)
    frequency_score = _inverse_linear(metrics.avg_trades_per_day, 3.0, 25.0)
    min_order_score = _linear(metrics.avg_position_size_usd, 50.0, 500.0)
    return _clamp(
        0.30 * size_score
        + 0.25 * holding_time_score
        + 0.20 * liquidity_proxy_score
        + 0.15 * frequency_score
        + 0.10 * min_order_score
    )


def _diversification_score(metrics: CandidateMetrics) -> float:
    long_ratio_balance = (
        100.0
        - abs(
            (metrics.long_ratio_pct if metrics.long_ratio_pct is not None else 50.0)
            - 50.0
        )
        * 2.0
    )
    volatility_score = _inverse_linear(metrics.daily_pnl_std_dev, 0.0, 2_500.0)
    known_history_bonus = 80.0 if metrics.daily_pnl_by_day else 60.0
    return _clamp(
        0.45 * long_ratio_balance + 0.35 * volatility_score + 0.20 * known_history_bonus
    )


def _behavior_stability_score(metrics: CandidateMetrics) -> float:
    active_days_score = _linear(
        (
            float(metrics.active_trading_days)
            if metrics.active_trading_days is not None
            else None
        ),
        30.0,
        180.0,
    )
    trade_frequency_score = _inverse_linear(metrics.avg_trades_per_day, 5.0, 25.0)
    leverage_score = _inverse_linear(metrics.avg_leverage, 1.0, 8.0)
    drawdown_duration_score = _inverse_linear(
        metrics.max_drawdown_duration_days, 0.0, 45.0
    )
    return _clamp(
        0.30 * active_days_score
        + 0.25 * trade_frequency_score
        + 0.25 * leverage_score
        + 0.20 * drawdown_duration_score
    )


def score_candidate(candidate: PortfolioCandidate) -> ScoredCandidate:
    metrics = candidate.metrics
    components = {
        "risk_adjusted_score": round(_risk_adjusted_score(metrics), 4),
        "consistency_score": round(_consistency_score(metrics), 4),
        "return_score": round(_return_score(metrics), 4),
        "copyability_score": round(_copyability_score(metrics), 4),
        "diversification_score": round(_diversification_score(metrics), 4),
        "behavior_stability_score": round(_behavior_stability_score(metrics), 4),
    }
    base_portfolio_score = round(
        0.30 * components["risk_adjusted_score"]
        + 0.25 * components["consistency_score"]
        + 0.15 * components["return_score"]
        + 0.15 * components["copyability_score"]
        + 0.10 * components["diversification_score"]
        + 0.05 * components["behavior_stability_score"],
        4,
    )
    anomaly_detection = detect_candidate_anomalies(metrics)
    anomaly_penalty = float(anomaly_detection["penalty"])
    portfolio_score = round(_clamp(base_portfolio_score - anomaly_penalty), 4)
    strategy_profile = classify_strategy_profile(metrics)
    snapshot = {
        "methodology_version": "balanced-advanced-v2",
        "portfolio_score": portfolio_score,
        "base_portfolio_score": base_portfolio_score,
        "component_scores": components,
        "anomaly_detection": anomaly_detection,
        "strategy_profile": strategy_profile,
        "source_metrics": {
            "pnl_usd": metrics.pnl_usd,
            "roi_pct": metrics.roi_pct,
            "volume_usd": metrics.volume_usd,
            "win_rate_pct": metrics.win_rate_pct,
            "composite_score": metrics.composite_score,
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "profit_factor": metrics.profit_factor,
            "profitable_days_pct": metrics.profitable_days_pct,
            "daily_pnl_std_dev": metrics.daily_pnl_std_dev,
            "long_ratio_pct": metrics.long_ratio_pct,
            "fees_paid_usd": metrics.fees_paid_usd,
            "calmar_ratio": metrics.calmar_ratio,
            "active_trading_days": metrics.active_trading_days,
            "trade_count": metrics.trade_count,
            "avg_leverage": metrics.avg_leverage,
            "avg_trades_per_day": metrics.avg_trades_per_day,
            "avg_position_size_usd": metrics.avg_position_size_usd,
        },
    }
    return ScoredCandidate(candidate, portfolio_score, components, snapshot)


def score_candidates(
    candidates: Iterable[PortfolioCandidate],
) -> tuple[ScoredCandidate, ...]:
    return tuple(score_candidate(candidate) for candidate in candidates)

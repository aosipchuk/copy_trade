from app.services.portfolio.candidates import apply_candidate_filters
from app.services.portfolio.optimizer import optimize_portfolio
from app.services.portfolio.scoring import score_candidate
from app.services.portfolio.types import (
    RISK_PROFILE_CONFIGS,
    CandidateMetrics,
    PortfolioCandidate,
    RawTraderCandidate,
    ScoredCandidate,
    get_internal_alpha_relaxed_config,
)


def _metrics(
    *,
    composite_score: float = 82.0,
    max_drawdown_pct: float = 18.0,
    avg_leverage: float = 3.0,
    avg_trades_per_day: float = 4.0,
    daily_pnl_by_day: dict[str, float] | None = None,
) -> CandidateMetrics:
    return CandidateMetrics(
        pnl_usd=25_000.0,
        roi_pct=24.0,
        volume_usd=1_250_000.0,
        win_rate_pct=61.0,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=80,
        avg_trade_duration_hrs=8.0,
        sharpe_ratio=1.8,
        sortino_ratio=2.4,
        profit_factor=1.9,
        avg_pnl_per_trade=85.0,
        max_losing_streak=3,
        profitable_days_pct=62.0,
        avg_trades_per_day=avg_trades_per_day,
        daily_pnl_std_dev=900.0,
        long_ratio_pct=52.0,
        avg_position_size_usd=4_000.0,
        fees_paid_usd=400.0,
        calmar_ratio=1.7,
        composite_score=composite_score,
        max_drawdown_duration_days=14.0,
        active_trading_days=75,
        avg_leverage=avg_leverage,
        daily_pnl_by_day=daily_pnl_by_day,
    )


def _raw_candidate(
    trader_id: int,
    *,
    is_active: bool = True,
    has_perp_activity: bool | None = True,
    metrics: CandidateMetrics | None = None,
) -> RawTraderCandidate:
    return RawTraderCandidate(
        trader_id=trader_id,
        hl_address=f"0x{trader_id:040x}",
        display_name=f"Trader {trader_id}",
        is_active=is_active,
        has_perp_activity=has_perp_activity,
        metrics=metrics or _metrics(),
    )


def _candidate(
    trader_id: int,
    *,
    metrics: CandidateMetrics | None = None,
) -> PortfolioCandidate:
    return PortfolioCandidate(
        trader_id=trader_id,
        hl_address=f"0x{trader_id:040x}",
        display_name=f"Trader {trader_id}",
        metrics=metrics or _metrics(),
        constraint_snapshot={"risk_profile": "balanced"},
    )


def _scored_candidate(
    trader_id: int,
    portfolio_score: float,
    *,
    daily_pnl_by_day: dict[str, float] | None = None,
) -> ScoredCandidate:
    candidate = _candidate(
        trader_id,
        metrics=_metrics(
            composite_score=portfolio_score,
            max_drawdown_pct=15.0 + trader_id,
            daily_pnl_by_day=daily_pnl_by_day,
        ),
    )
    return ScoredCandidate(
        candidate=candidate,
        portfolio_score=portfolio_score,
        component_scores={},
        score_snapshot={
            "portfolio_score": portfolio_score,
            "source_metrics": {"composite_score": portfolio_score},
        },
    )


def test_candidate_filters_reject_non_mvp_traders() -> None:
    config = RISK_PROFILE_CONFIGS["balanced"]
    result = apply_candidate_filters(
        [
            _raw_candidate(1),
            _raw_candidate(2, has_perp_activity=False),
            _raw_candidate(3, metrics=_metrics(composite_score=69.9)),
            _raw_candidate(4, metrics=_metrics(avg_leverage=8.1)),
            _raw_candidate(5, metrics=_metrics(avg_trades_per_day=21.0)),
        ],
        config,
    )

    assert [candidate.trader_id for candidate in result.eligible] == [1]
    assert [candidate.reason_code for candidate in result.rejected] == [
        "no_perp_activity",
        "low_composite_score",
        "leverage_too_high",
        "trade_frequency_too_high",
    ]


def test_candidate_filters_require_leverage_in_strict_mode() -> None:
    config = RISK_PROFILE_CONFIGS["balanced"]
    result = apply_candidate_filters(
        [_raw_candidate(1, metrics=_metrics(avg_leverage=None))],
        config,
    )

    assert result.eligible == ()
    assert result.rejected[0].reason_code == "missing_metrics"
    assert "avg_leverage" in result.rejected[0].reason_text


def test_candidate_filters_allow_unknown_leverage_in_internal_alpha_mode() -> None:
    config = get_internal_alpha_relaxed_config(RISK_PROFILE_CONFIGS["balanced"])
    result = apply_candidate_filters(
        [_raw_candidate(1, metrics=_metrics(avg_leverage=None))],
        config,
    )

    assert [candidate.trader_id for candidate in result.eligible] == [1]
    assert result.eligible[0].constraint_snapshot["require_avg_leverage"] is False


def test_candidate_filters_reject_known_high_leverage_in_internal_alpha_mode() -> None:
    config = get_internal_alpha_relaxed_config(RISK_PROFILE_CONFIGS["balanced"])
    result = apply_candidate_filters(
        [_raw_candidate(1, metrics=_metrics(avg_leverage=28.33))],
        config,
    )

    assert result.eligible == ()
    assert result.rejected[0].reason_code == "leverage_too_high"


def test_portfolio_score_is_stable_and_preserves_source_facts() -> None:
    candidate = _candidate(1)

    first = score_candidate(candidate)
    second = score_candidate(candidate)

    assert first.portfolio_score == second.portfolio_score
    assert first.score_snapshot == second.score_snapshot
    assert first.score_snapshot["source_metrics"]["composite_score"] == 82.0
    assert first.score_snapshot["component_scores"]["copyability_score"] > 0


def test_optimizer_weights_sum_to_100_and_respect_max_weight() -> None:
    config = RISK_PROFILE_CONFIGS["balanced"]
    candidates = tuple(
        _scored_candidate(trader_id, 95.0 - trader_id) for trader_id in range(1, 11)
    )

    result = optimize_portfolio(candidates, config)

    assert len(result.allocations) == 10
    assert round(sum(item.target_weight_pct for item in result.allocations), 3) == 100.0
    assert max(item.target_weight_pct for item in result.allocations) <= 18.0
    assert result.summary["target_weight_sum_pct"] == 100.0


def test_optimizer_rejects_high_correlation_candidate() -> None:
    config = RISK_PROFILE_CONFIGS["balanced"]
    base_series = {f"2026-01-{day:02d}": float(day) for day in range(1, 12)}
    correlated_series = {day: value * 2.0 for day, value in base_series.items()}
    candidates = (
        _scored_candidate(1, 95.0, daily_pnl_by_day=base_series),
        _scored_candidate(2, 94.0, daily_pnl_by_day=correlated_series),
        _scored_candidate(3, 83.0),
        _scored_candidate(4, 82.0),
        _scored_candidate(5, 81.0),
        _scored_candidate(6, 80.0),
        _scored_candidate(7, 79.0),
        _scored_candidate(8, 78.0),
    )

    result = optimize_portfolio(candidates, config)

    allocation_trader_ids = {
        allocation.scored_candidate.candidate.trader_id
        for allocation in result.allocations
    }
    assert 2 not in allocation_trader_ids
    assert any(
        rejected.trader_id == 2 and rejected.reason_code == "high_correlation"
        for rejected in result.rejected
    )

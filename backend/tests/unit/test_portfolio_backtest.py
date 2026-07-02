import pytest

from app.services.portfolio.backtest import (
    BacktestAllocationSnapshot,
    BacktestAssumptions,
    compute_model_portfolio_backtest,
)


def test_backtest_uses_daily_return_fixture_without_proxy() -> None:
    allocations = (
        BacktestAllocationSnapshot(
            allocation_id=1,
            trader_id=101,
            target_weight_pct=60.0,
            source_metrics={
                "daily_returns_pct_by_day": {
                    "2026-01-01": 1.0,
                    "2026-01-02": -1.0,
                    "2026-01-03": 2.0,
                    "2026-01-04": 0.0,
                }
            },
        ),
        BacktestAllocationSnapshot(
            allocation_id=2,
            trader_id=102,
            target_weight_pct=40.0,
            source_metrics={
                "daily_returns_pct_by_day": {
                    "2026-01-01": 0.0,
                    "2026-01-02": 1.0,
                    "2026-01-03": -1.0,
                    "2026-01-04": 1.0,
                }
            },
        ),
    )

    result = compute_model_portfolio_backtest(
        allocations,
        assumptions=BacktestAssumptions(
            period_days=4,
            initial_equity_usd=1000.0,
            fees_bps=0.0,
            slippage_bps=0.0,
        ),
    )

    assert result.assumptions_json["data_source"] == "daily_snapshot"
    assert result.total_return_pct == pytest.approx(1.6068)
    assert result.max_drawdown_pct == pytest.approx(0.2)
    assert result.win_rate_pct == 75.0
    assert result.equity_curve_json["points"][-1]["equity"] == pytest.approx(
        1016.068,
        abs=0.001,
    )


def test_backtest_proxy_marks_limited_data_assumptions() -> None:
    allocations = (
        BacktestAllocationSnapshot(
            allocation_id=1,
            trader_id=101,
            target_weight_pct=100.0,
            source_metrics={
                "roi_pct": 12.0,
                "active_trading_days": 120,
                "max_drawdown_pct": 8.5,
            },
        ),
    )

    result = compute_model_portfolio_backtest(
        allocations,
        assumptions=BacktestAssumptions(
            period_days=60,
            initial_equity_usd=1000.0,
            fees_bps=0.0,
            slippage_bps=0.0,
        ),
    )

    assert result.assumptions_json["data_source"] == "aggregate_metric_proxy"
    assert result.assumptions_json["uses_trade_level_fills"] is False
    assert result.total_return_pct == pytest.approx(6.0, abs=0.01)
    assert result.max_drawdown_pct == 8.5

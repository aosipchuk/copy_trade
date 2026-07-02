from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import configure_mappers

from app.core.database import Base
from app.schemas.portfolio import (
    ModelPortfolioResponse,
    ModelPortfolioVersionDetailResponse,
    PortfolioBacktestResponse,
)
from scripts.seed_model_portfolios import BALANCED_PORTFOLIO


def test_portfolio_models_are_registered_in_metadata() -> None:
    configure_mappers()

    expected_tables = {
        "model_portfolios",
        "model_portfolio_versions",
        "model_portfolio_allocations",
        "user_portfolio_subscriptions",
        "user_portfolio_items",
        "portfolio_rebalance_events",
        "portfolio_backtests",
        "portfolio_reports",
    }
    assert expected_tables.issubset(Base.metadata.tables)

    subscription_columns = Base.metadata.tables["subscriptions"].c
    assert "source_type" in subscription_columns
    assert "source_id" in subscription_columns
    assert "source_version_id" in subscription_columns
    assert "managed_by_portfolio" in subscription_columns
    assert subscription_columns.source_type.server_default is not None
    assert subscription_columns.managed_by_portfolio.server_default is not None


def test_portfolio_schema_serializes_orm_like_objects() -> None:
    now = datetime(2026, 7, 2, 12, 0, 0)
    portfolio = SimpleNamespace(
        id=1,
        slug="balanced",
        name="Balanced",
        risk_profile="balanced",
        status="active",
        description="Balanced model portfolio.",
        methodology_version="balanced-mvp-v1",
        rebalance_cadence="weekly",
        min_equity_usd=Decimal("1000.00"),
        monthly_price_usd=Decimal("19.00"),
        trial_days=7,
        created_at=now,
        updated_at=now,
    )

    response = ModelPortfolioResponse.model_validate(portfolio)

    assert response.slug == "balanced"
    assert response.risk_profile == "balanced"
    assert response.monthly_price_usd == 19.0


def test_version_detail_schema_includes_allocations() -> None:
    now = datetime(2026, 7, 2, 12, 0, 0)
    allocation = SimpleNamespace(
        id=10,
        version_id=2,
        trader_id=99,
        target_weight_pct=Decimal("16.667"),
        copy_ratio_pct=Decimal("100.00"),
        max_leverage=Decimal("8.00"),
        stop_loss_pct=Decimal("20.00"),
        sizing_mode="fixed_ratio",
        max_per_coin_usd=None,
        allowed_coins=["BTC", "ETH"],
        reason_code="risk_adjusted_score",
        reason_text="Selected by deterministic Balanced methodology.",
        score_snapshot={"portfolio_score": 82.5},
        constraint_snapshot={"max_correlation": 0.65},
        created_at=now,
    )
    version = SimpleNamespace(
        id=2,
        portfolio_id=1,
        version_no=1,
        status="published",
        valid_from=now,
        valid_to=None,
        created_by=None,
        approved_by=1,
        approved_at=now,
        approval_note="Approved for internal alpha.",
        selection_started_at=now,
        selection_finished_at=now,
        facts_hash="facts-v1",
        summary_json={"trader_count": 6},
        created_at=now,
        allocations=[allocation],
    )

    response = ModelPortfolioVersionDetailResponse.model_validate(version)

    assert response.version_no == 1
    assert response.allocations[0].target_weight_pct == 16.667
    assert response.allocations[0].score_snapshot == {"portfolio_score": 82.5}


def test_backtest_schema_preserves_assumptions() -> None:
    now = datetime(2026, 7, 2, 12, 0, 0)
    backtest = SimpleNamespace(
        id=5,
        portfolio_version_id=2,
        period_days=180,
        initial_equity_usd=Decimal("10000.00"),
        total_return_pct=Decimal("12.3400"),
        max_drawdown_pct=Decimal("8.5000"),
        sharpe_ratio=Decimal("1.2500"),
        sortino_ratio=Decimal("1.6000"),
        win_rate_pct=Decimal("55.00"),
        turnover_pct=Decimal("18.2000"),
        fees_usd=Decimal("42.0000"),
        slippage_usd=Decimal("25.0000"),
        missed_trade_count=3,
        assumptions_json={"fees_bps": 4, "slippage_bps": 5},
        equity_curve_json={"points": []},
        created_at=now,
    )

    response = PortfolioBacktestResponse.model_validate(backtest)

    assert response.initial_equity_usd == 10000.0
    assert response.assumptions_json["fees_bps"] == 4
    assert response.missed_trade_count == 3


def test_balanced_seed_matches_phase_zero_decisions() -> None:
    assert BALANCED_PORTFOLIO["slug"] == "balanced"
    assert BALANCED_PORTFOLIO["risk_profile"] == "balanced"
    assert BALANCED_PORTFOLIO["status"] == "active"
    assert BALANCED_PORTFOLIO["monthly_price_usd"] == Decimal("19.00")
    assert BALANCED_PORTFOLIO["methodology_version"] == "balanced-mvp-v1"

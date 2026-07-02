from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

RiskProfile = Literal["conservative", "balanced", "aggressive"]
JsonDict = dict[str, Any]


@dataclass(frozen=True)
class RiskProfileConfig:
    risk_profile: RiskProfile
    min_traders: int
    max_traders: int
    max_weight_pct: float
    min_composite_score: float
    max_drawdown_pct: float
    max_leverage: float
    min_active_trading_days: int
    min_trade_count: int
    max_correlation: float
    max_avg_trades_per_day: float
    default_stop_loss_pct: float
    require_avg_leverage: bool = True


RISK_PROFILE_CONFIGS: dict[RiskProfile, RiskProfileConfig] = {
    "conservative": RiskProfileConfig(
        risk_profile="conservative",
        min_traders=5,
        max_traders=7,
        max_weight_pct=20.0,
        min_composite_score=78.0,
        max_drawdown_pct=20.0,
        max_leverage=4.0,
        min_active_trading_days=45,
        min_trade_count=30,
        max_correlation=0.55,
        max_avg_trades_per_day=12.0,
        default_stop_loss_pct=15.0,
    ),
    "balanced": RiskProfileConfig(
        risk_profile="balanced",
        min_traders=6,
        max_traders=10,
        max_weight_pct=18.0,
        min_composite_score=70.0,
        max_drawdown_pct=35.0,
        max_leverage=8.0,
        min_active_trading_days=30,
        min_trade_count=20,
        max_correlation=0.65,
        max_avg_trades_per_day=20.0,
        default_stop_loss_pct=20.0,
    ),
    "aggressive": RiskProfileConfig(
        risk_profile="aggressive",
        min_traders=8,
        max_traders=12,
        max_weight_pct=15.0,
        min_composite_score=65.0,
        max_drawdown_pct=50.0,
        max_leverage=15.0,
        min_active_trading_days=20,
        min_trade_count=20,
        max_correlation=0.75,
        max_avg_trades_per_day=30.0,
        default_stop_loss_pct=25.0,
    ),
}


def get_risk_profile_config(risk_profile: str) -> RiskProfileConfig:
    try:
        return RISK_PROFILE_CONFIGS[risk_profile]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"Unsupported risk profile: {risk_profile}") from exc


def get_internal_alpha_relaxed_config(
    config: RiskProfileConfig,
) -> RiskProfileConfig:
    """Relax only data-availability gates for internal draft testing.

    This does not change the default Balanced methodology. It is used explicitly
    by the CLI when production metrics are too sparse to validate draft writes.
    Drafts built with this mode still require manual review before publication.
    """
    return replace(
        config,
        min_composite_score=min(config.min_composite_score, 65.0),
        min_active_trading_days=min(config.min_active_trading_days, 10),
        require_avg_leverage=False,
    )


@dataclass(frozen=True)
class CandidateMetrics:
    pnl_usd: float | None = None
    roi_pct: float | None = None
    volume_usd: float | None = None
    win_rate_pct: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    avg_trade_duration_hrs: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    profit_factor: float | None = None
    avg_pnl_per_trade: float | None = None
    max_losing_streak: int | None = None
    profitable_days_pct: float | None = None
    avg_trades_per_day: float | None = None
    daily_pnl_std_dev: float | None = None
    long_ratio_pct: float | None = None
    avg_position_size_usd: float | None = None
    fees_paid_usd: float | None = None
    calmar_ratio: float | None = None
    composite_score: float | None = None
    max_drawdown_duration_days: float | None = None
    active_trading_days: int | None = None
    avg_leverage: float | None = None
    daily_pnl_by_day: Mapping[str, float] | None = None


@dataclass(frozen=True)
class RawTraderCandidate:
    trader_id: int
    hl_address: str
    display_name: str | None
    is_active: bool
    has_perp_activity: bool | None
    metrics: CandidateMetrics


@dataclass(frozen=True)
class PortfolioCandidate:
    trader_id: int
    hl_address: str
    display_name: str | None
    metrics: CandidateMetrics
    constraint_snapshot: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedCandidate:
    trader_id: int
    reason_code: str
    reason_text: str
    constraint_snapshot: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateSelectionResult:
    eligible: tuple[PortfolioCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: PortfolioCandidate
    portfolio_score: float
    component_scores: JsonDict
    score_snapshot: JsonDict


@dataclass(frozen=True)
class OptimizedAllocation:
    scored_candidate: ScoredCandidate
    target_weight_pct: float
    copy_ratio_pct: float
    max_leverage: float
    stop_loss_pct: float
    sizing_mode: str
    max_per_coin_usd: float | None
    allowed_coins: list[str] | None
    reason_code: str
    reason_text: str
    constraint_snapshot: JsonDict


@dataclass(frozen=True)
class OptimizationResult:
    allocations: tuple[OptimizedAllocation, ...]
    rejected: tuple[RejectedCandidate, ...]
    summary: JsonDict

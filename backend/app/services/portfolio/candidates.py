from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trader import Trader, TraderStat
from app.services.portfolio.types import (
    CandidateMetrics,
    CandidateSelectionResult,
    PortfolioCandidate,
    RawTraderCandidate,
    RejectedCandidate,
    RiskProfileConfig,
)

ESSENTIAL_METRICS = (
    "composite_score",
    "trade_count",
    "active_trading_days",
    "max_drawdown_pct",
    "avg_trades_per_day",
)


def _float(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]


def metrics_from_stat(stat: TraderStat) -> CandidateMetrics:
    return CandidateMetrics(
        pnl_usd=_float(stat.pnl_usd),
        roi_pct=_float(stat.roi_pct),
        volume_usd=_float(stat.volume_usd),
        win_rate_pct=_float(stat.win_rate_pct),
        max_drawdown_pct=_float(stat.max_drawdown_pct),
        trade_count=stat.trade_count,
        avg_trade_duration_hrs=_float(stat.avg_trade_duration_hrs),
        sharpe_ratio=_float(stat.sharpe_ratio),
        sortino_ratio=_float(stat.sortino_ratio),
        profit_factor=_float(stat.profit_factor),
        avg_pnl_per_trade=_float(stat.avg_pnl_per_trade),
        max_losing_streak=stat.max_losing_streak,
        profitable_days_pct=_float(stat.profitable_days_pct),
        avg_trades_per_day=_float(stat.avg_trades_per_day),
        daily_pnl_std_dev=_float(stat.daily_pnl_std_dev),
        long_ratio_pct=_float(stat.long_ratio_pct),
        avg_position_size_usd=_float(stat.avg_position_size_usd),
        fees_paid_usd=_float(stat.fees_paid_usd),
        calmar_ratio=_float(stat.calmar_ratio),
        composite_score=_float(stat.composite_score),
        max_drawdown_duration_days=_float(stat.max_drawdown_duration_days),
        active_trading_days=stat.active_trading_days,
        avg_leverage=_float(stat.avg_leverage),
    )


def raw_candidate_from_models(trader: Trader, stat: TraderStat) -> RawTraderCandidate:
    return RawTraderCandidate(
        trader_id=trader.id,
        hl_address=trader.hl_address,
        display_name=trader.display_name,
        is_active=trader.is_active,
        has_perp_activity=trader.has_perp_activity,
        metrics=metrics_from_stat(stat),
    )


def _config_snapshot(config: RiskProfileConfig) -> dict[str, Any]:
    return {
        "risk_profile": config.risk_profile,
        "min_composite_score": config.min_composite_score,
        "min_trade_count": config.min_trade_count,
        "min_active_trading_days": config.min_active_trading_days,
        "max_drawdown_pct": config.max_drawdown_pct,
        "max_leverage": config.max_leverage,
        "require_avg_leverage": config.require_avg_leverage,
        "max_avg_trades_per_day": config.max_avg_trades_per_day,
    }


def _candidate_snapshot(
    raw: RawTraderCandidate, config: RiskProfileConfig
) -> dict[str, Any]:
    metrics = raw.metrics
    return {
        **_config_snapshot(config),
        "observed": {
            "is_active": raw.is_active,
            "has_perp_activity": raw.has_perp_activity,
            "composite_score": metrics.composite_score,
            "trade_count": metrics.trade_count,
            "active_trading_days": metrics.active_trading_days,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "avg_leverage": metrics.avg_leverage,
            "avg_trades_per_day": metrics.avg_trades_per_day,
        },
    }


def _reject(
    raw: RawTraderCandidate,
    config: RiskProfileConfig,
    reason_code: str,
    reason_text: str,
) -> RejectedCandidate:
    return RejectedCandidate(
        trader_id=raw.trader_id,
        reason_code=reason_code,
        reason_text=reason_text,
        constraint_snapshot=_candidate_snapshot(raw, config),
    )


def _missing_metrics(metrics: CandidateMetrics, config: RiskProfileConfig) -> list[str]:
    required_metrics = list(ESSENTIAL_METRICS)
    if config.require_avg_leverage:
        required_metrics.append("avg_leverage")
    return [
        field_name
        for field_name in required_metrics
        if getattr(metrics, field_name) is None
    ]


def apply_candidate_filters(
    raw_candidates: Iterable[RawTraderCandidate],
    config: RiskProfileConfig,
) -> CandidateSelectionResult:
    eligible: list[PortfolioCandidate] = []
    rejected: list[RejectedCandidate] = []

    for raw in raw_candidates:
        metrics = raw.metrics
        if not raw.is_active:
            rejected.append(_reject(raw, config, "inactive", "Trader is not active."))
            continue
        if raw.has_perp_activity is not True:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "no_perp_activity",
                    "Trader has no confirmed perp activity for copy trading.",
                )
            )
            continue

        missing = _missing_metrics(metrics, config)
        if missing:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "missing_metrics",
                    f"Trader is missing required metrics: {', '.join(missing)}.",
                )
            )
            continue

        composite_score = metrics.composite_score or 0.0
        trade_count = metrics.trade_count or 0
        active_days = metrics.active_trading_days or 0
        max_drawdown = metrics.max_drawdown_pct or 0.0
        avg_leverage = metrics.avg_leverage
        avg_trades_per_day = metrics.avg_trades_per_day or 0.0

        if composite_score < config.min_composite_score:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "low_composite_score",
                    "Trader composite score is below the portfolio threshold.",
                )
            )
            continue
        if trade_count < config.min_trade_count:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "low_trade_count",
                    "Trader has too few closed trades for this portfolio.",
                )
            )
            continue
        if active_days < config.min_active_trading_days:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "low_active_trading_days",
                    "Trader has too few active trading days for this portfolio.",
                )
            )
            continue
        if max_drawdown > config.max_drawdown_pct:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "drawdown_too_high",
                    "Trader drawdown exceeds the portfolio limit.",
                )
            )
            continue
        if avg_leverage is not None and avg_leverage > config.max_leverage:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "leverage_too_high",
                    "Trader average leverage exceeds the portfolio limit.",
                )
            )
            continue
        if avg_trades_per_day > config.max_avg_trades_per_day:
            rejected.append(
                _reject(
                    raw,
                    config,
                    "trade_frequency_too_high",
                    "Trader trade frequency is too high for reliable copy trading.",
                )
            )
            continue

        eligible.append(
            PortfolioCandidate(
                trader_id=raw.trader_id,
                hl_address=raw.hl_address,
                display_name=raw.display_name,
                metrics=metrics,
                constraint_snapshot=_candidate_snapshot(raw, config),
            )
        )

    return CandidateSelectionResult(tuple(eligible), tuple(rejected))


async def load_portfolio_candidates(
    db: AsyncSession,
    config: RiskProfileConfig,
    period: str = "allTime",
) -> CandidateSelectionResult:
    stmt = (
        select(Trader, TraderStat)
        .join(TraderStat, TraderStat.trader_id == Trader.id)
        .where(TraderStat.period == period)
        .order_by(TraderStat.composite_score.desc().nullslast(), Trader.id.asc())
    )
    result = await db.execute(stmt)
    raw_candidates = [
        raw_candidate_from_models(trader, stat) for trader, stat in result.all()
    ]
    return apply_candidate_filters(raw_candidates, config)

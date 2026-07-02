import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.portfolio import (
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioBacktest,
)

JsonDict = dict[str, Any]

DEFAULT_FEES_BPS = 4.0
DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_MIN_ORDER_SIZE_USD = 10.0
DEFAULT_REBALANCE_CADENCE = "weekly"


@dataclass(frozen=True)
class BacktestAllocationSnapshot:
    allocation_id: int
    trader_id: int
    target_weight_pct: float
    source_metrics: Mapping[str, Any]


@dataclass(frozen=True)
class BacktestAssumptions:
    period_days: int = 180
    initial_equity_usd: float = 10_000.0
    fees_bps: float = DEFAULT_FEES_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    min_order_size_usd: float = DEFAULT_MIN_ORDER_SIZE_USD
    rebalance_cadence: str = DEFAULT_REBALANCE_CADENCE


@dataclass(frozen=True)
class BacktestComputation:
    period_days: int
    initial_equity_usd: float
    total_return_pct: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    win_rate_pct: float | None
    turnover_pct: float
    fees_usd: float
    slippage_usd: float
    missed_trade_count: int
    assumptions_json: JsonDict
    equity_curve_json: JsonDict


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _daily_returns_from_metrics(
    source_metrics: Mapping[str, Any],
) -> dict[str, float] | None:
    raw = source_metrics.get("daily_returns_pct_by_day")
    if not isinstance(raw, Mapping):
        return None

    points: dict[str, float] = {}
    for day, value in raw.items():
        if not isinstance(day, str):
            continue
        daily_return = _float(value)
        if daily_return is None:
            continue
        points[day] = daily_return
    return points or None


def _portfolio_daily_returns(
    allocations: Sequence[BacktestAllocationSnapshot],
    period_days: int,
) -> tuple[list[tuple[str, float]], int]:
    weighted_by_day: dict[str, float] = {}
    missing_series = 0

    for allocation in allocations:
        returns = _daily_returns_from_metrics(allocation.source_metrics)
        if not returns:
            missing_series += 1
            continue
        weight = allocation.target_weight_pct / 100.0
        for day, daily_return_pct in returns.items():
            weighted_by_day[day] = weighted_by_day.get(day, 0.0) + (
                daily_return_pct * weight
            )

    if not weighted_by_day:
        return [], missing_series

    ordered = sorted(weighted_by_day.items())[-period_days:]
    return ordered, missing_series


def _proxy_total_return_pct(
    allocations: Sequence[BacktestAllocationSnapshot],
    period_days: int,
) -> float | None:
    total = 0.0
    observed = False
    for allocation in allocations:
        roi_pct = _float(allocation.source_metrics.get("roi_pct"))
        if roi_pct is None:
            continue
        active_days = _float(allocation.source_metrics.get("active_trading_days"))
        scale = 1.0
        if active_days is not None and active_days > 0:
            scale = min(1.0, period_days / active_days)
        total += roi_pct * scale * allocation.target_weight_pct / 100.0
        observed = True
    if not observed:
        return None
    return round(total, 4)


def _proxy_max_drawdown_pct(
    allocations: Sequence[BacktestAllocationSnapshot],
) -> float | None:
    total = 0.0
    observed = False
    for allocation in allocations:
        max_drawdown_pct = _float(allocation.source_metrics.get("max_drawdown_pct"))
        if max_drawdown_pct is None:
            continue
        total += max_drawdown_pct * allocation.target_weight_pct / 100.0
        observed = True
    if not observed:
        return None
    return round(total, 4)


def _proxy_daily_returns(
    total_return_pct: float | None, period_days: int
) -> list[float]:
    if total_return_pct is None or period_days <= 0:
        return [0.0 for _ in range(max(period_days, 0))]
    daily_multiplier = (1.0 + total_return_pct / 100.0) ** (1.0 / period_days)
    daily_return_pct = (daily_multiplier - 1.0) * 100.0
    return [daily_return_pct for _ in range(period_days)]


def _equity_curve(
    daily_returns: Sequence[float],
    initial_equity_usd: float,
    daily_drag_pct: float,
    labels: Sequence[str] | None = None,
) -> list[JsonDict]:
    equity = initial_equity_usd
    peak = initial_equity_usd
    points: list[JsonDict] = []

    for index, gross_return_pct in enumerate(daily_returns, start=1):
        net_return_pct = gross_return_pct - daily_drag_pct
        equity *= 1.0 + net_return_pct / 100.0
        peak = max(peak, equity)
        drawdown_pct = 0.0 if peak <= 0 else (peak - equity) / peak * 100.0
        point: JsonDict = {
            "index": index,
            "equity": round(equity, 4),
            "return_pct": round(net_return_pct, 6),
            "drawdown_pct": round(drawdown_pct, 6),
        }
        if labels and index <= len(labels):
            point["day"] = labels[index - 1]
        points.append(point)

    return points


def _max_drawdown_from_curve(points: Sequence[JsonDict]) -> float | None:
    if not points:
        return None
    return round(
        max(float(point.get("drawdown_pct", 0.0)) for point in points),
        4,
    )


def _risk_ratio(
    daily_returns_pct: Sequence[float], downside_only: bool
) -> float | None:
    if len(daily_returns_pct) < 2:
        return None

    returns = [value / 100.0 for value in daily_returns_pct]
    mean_return = sum(returns) / len(returns)
    samples = [min(0.0, value) for value in returns] if downside_only else returns
    variance = sum((value - mean_return) ** 2 for value in samples) / (len(samples) - 1)
    if variance <= 0:
        return None
    ratio = mean_return / math.sqrt(variance) * math.sqrt(365)
    return round(ratio, 4)


def _turnover_pct(period_days: int, rebalance_cadence: str) -> float:
    if rebalance_cadence == "weekly":
        rebalance_count = max(0, period_days // 7)
        return round(100.0 + rebalance_count * 10.0, 4)
    return 100.0


def compute_model_portfolio_backtest(
    allocations: Sequence[BacktestAllocationSnapshot],
    assumptions: BacktestAssumptions,
) -> BacktestComputation:
    if assumptions.period_days <= 0:
        raise ValueError("period_days must be positive.")
    if assumptions.initial_equity_usd <= 0:
        raise ValueError("initial_equity_usd must be positive.")
    if not allocations:
        raise ValueError("At least one allocation is required.")

    weighted_returns, missing_series_count = _portfolio_daily_returns(
        allocations, assumptions.period_days
    )
    if weighted_returns:
        data_source = "daily_snapshot"
        labels = [day for day, _ in weighted_returns]
        daily_returns = [daily_return for _, daily_return in weighted_returns]
        proxy_total_return_pct = None
    else:
        data_source = "aggregate_metric_proxy"
        labels = None
        proxy_total_return_pct = _proxy_total_return_pct(
            allocations, assumptions.period_days
        )
        daily_returns = _proxy_daily_returns(
            proxy_total_return_pct, assumptions.period_days
        )

    turnover_pct = _turnover_pct(assumptions.period_days, assumptions.rebalance_cadence)
    fees_usd = (
        assumptions.initial_equity_usd * (turnover_pct / 100.0) * assumptions.fees_bps
    ) / 10_000.0
    slippage_usd = (
        assumptions.initial_equity_usd
        * (turnover_pct / 100.0)
        * assumptions.slippage_bps
    ) / 10_000.0
    total_drag_pct = (
        (turnover_pct / 100.0) * (assumptions.fees_bps + assumptions.slippage_bps)
    ) / 100.0
    daily_drag_pct = total_drag_pct / len(daily_returns) if daily_returns else 0.0

    curve_points = _equity_curve(
        daily_returns,
        assumptions.initial_equity_usd,
        daily_drag_pct,
        labels=labels,
    )

    if curve_points:
        final_equity = float(curve_points[-1]["equity"])
        total_return_pct = round(
            (final_equity / assumptions.initial_equity_usd - 1.0) * 100.0,
            4,
        )
    else:
        total_return_pct = None

    if data_source == "aggregate_metric_proxy":
        max_drawdown_pct = _proxy_max_drawdown_pct(allocations)
    else:
        max_drawdown_pct = _max_drawdown_from_curve(curve_points)

    weighted_source_count = len(allocations) - missing_series_count
    missed_trade_count = sum(
        1
        for allocation in allocations
        if assumptions.initial_equity_usd * allocation.target_weight_pct / 100.0
        < assumptions.min_order_size_usd
    )

    if daily_returns:
        win_rate_pct = round(
            sum(1 for value in daily_returns if value > 0) / len(daily_returns) * 100.0,
            2,
        )
    else:
        win_rate_pct = None

    assumptions_json: JsonDict = {
        "engine": "portfolio_backtest_mvp_v1",
        "data_source": data_source,
        "period_days": assumptions.period_days,
        "initial_equity_usd": assumptions.initial_equity_usd,
        "fees_bps": assumptions.fees_bps,
        "slippage_bps": assumptions.slippage_bps,
        "turnover_model": f"{assumptions.rebalance_cadence}_10pct",
        "turnover_pct": turnover_pct,
        "minimum_order_size_usd": assumptions.min_order_size_usd,
        "uses_trade_level_fills": False,
        "lookahead_guard": (
            "Uses only allocation score_snapshot/source_metrics saved on the "
            "portfolio version."
        ),
        "weighted_source_count": weighted_source_count,
        "missing_daily_series_count": missing_series_count,
        "limitations": [
            "MVP backtest does not model order book depth or partial fills.",
            "Fees and slippage are deterministic assumptions.",
            "Aggregate-metric proxy is not a trade-level historical replay.",
        ],
    }

    equity_curve_json: JsonDict = {
        "source": data_source,
        "points": curve_points,
    }

    return BacktestComputation(
        period_days=assumptions.period_days,
        initial_equity_usd=assumptions.initial_equity_usd,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=_risk_ratio(daily_returns, downside_only=False),
        sortino_ratio=_risk_ratio(daily_returns, downside_only=True),
        win_rate_pct=win_rate_pct,
        turnover_pct=turnover_pct,
        fees_usd=round(fees_usd, 4),
        slippage_usd=round(slippage_usd, 4),
        missed_trade_count=missed_trade_count,
        assumptions_json=assumptions_json,
        equity_curve_json=equity_curve_json,
    )


def _source_metrics(allocation: ModelPortfolioAllocation) -> Mapping[str, Any]:
    score_snapshot = allocation.score_snapshot or {}
    source_metrics = score_snapshot.get("source_metrics")
    if isinstance(source_metrics, Mapping):
        return source_metrics
    return {}


async def get_portfolio_version_for_backtest(
    db: AsyncSession, version_id: int
) -> ModelPortfolioVersion:
    result = await db.execute(
        select(ModelPortfolioVersion)
        .options(selectinload(ModelPortfolioVersion.allocations))
        .where(ModelPortfolioVersion.id == version_id)
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise LookupError(f"Portfolio version not found: {version_id}")
    return version


async def run_model_portfolio_backtest(
    db: AsyncSession,
    version_id: int,
    assumptions: BacktestAssumptions,
    replace_existing: bool = True,
) -> PortfolioBacktest:
    version = await get_portfolio_version_for_backtest(db, version_id)
    allocation_snapshots = [
        BacktestAllocationSnapshot(
            allocation_id=allocation.id,
            trader_id=allocation.trader_id,
            target_weight_pct=float(allocation.target_weight_pct),
            source_metrics=_source_metrics(allocation),
        )
        for allocation in version.allocations
    ]
    computation = compute_model_portfolio_backtest(
        allocation_snapshots, assumptions=assumptions
    )

    if replace_existing:
        await db.execute(
            delete(PortfolioBacktest).where(
                PortfolioBacktest.portfolio_version_id == version_id,
                PortfolioBacktest.period_days == assumptions.period_days,
                PortfolioBacktest.initial_equity_usd
                == Decimal(str(assumptions.initial_equity_usd)),
            )
        )

    backtest = PortfolioBacktest(
        portfolio_version_id=version_id,
        period_days=computation.period_days,
        initial_equity_usd=Decimal(str(computation.initial_equity_usd)),
        total_return_pct=_decimal(computation.total_return_pct),
        max_drawdown_pct=_decimal(computation.max_drawdown_pct),
        sharpe_ratio=_decimal(computation.sharpe_ratio),
        sortino_ratio=_decimal(computation.sortino_ratio),
        win_rate_pct=_decimal(computation.win_rate_pct),
        turnover_pct=_decimal(computation.turnover_pct),
        fees_usd=_decimal(computation.fees_usd),
        slippage_usd=_decimal(computation.slippage_usd),
        missed_trade_count=computation.missed_trade_count,
        assumptions_json=computation.assumptions_json,
        equity_curve_json=computation.equity_curve_json,
    )
    db.add(backtest)
    await db.flush()
    return backtest


def naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

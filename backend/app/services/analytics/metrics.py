import asyncio
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.trader import Trader, TraderStat
from app.schemas.trader import (
    ClosedTradeItem,
    EquityPoint,
    FillItem,
    PositionItem,
    TraderStatSchema,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import Fill, Position

_position_adapter: TypeAdapter[list[Position]] = TypeAdapter(list[Position])

logger = get_logger(__name__)


def _f(val: object) -> float | None:
    """Cast a Numeric/Decimal column value to float, or return None."""
    return float(val) if val is not None else None  # type: ignore[arg-type]


def _is_perp_fill(f: Fill) -> bool:
    """True iff the fill belongs to a perp trade.

    Perp fills always reference a side in their direction (Open/Close Long/Short,
    "Long > Short"); spot ("Buy"/"Sell") and prediction-market
    ("Negate/Split/Merge Outcome") fills never do. All copy-trade analytics are
    computed strictly on perp fills because the copy engine mirrors only perps.
    """
    return "Long" in f.dir or "Short" in f.dir


async def get_trader_stats(db: AsyncSession, trader_id: int) -> list[TraderStatSchema]:
    result = await db.execute(
        select(TraderStat).where(TraderStat.trader_id == trader_id)
    )
    rows = result.scalars().all()
    return [
        TraderStatSchema(
            period=r.period,
            pnl_usd=_f(r.pnl_usd),
            roi_pct=_f(r.roi_pct),
            volume_usd=_f(r.volume_usd),
            win_rate_pct=_f(r.win_rate_pct),
            max_drawdown_usd=_f(r.max_drawdown_usd),
            max_drawdown_pct=_f(r.max_drawdown_pct),
            trade_count=r.trade_count,
            avg_trade_duration_hrs=_f(r.avg_trade_duration_hrs),
            first_trade_at=r.first_trade_at,
            sharpe_ratio=_f(r.sharpe_ratio),
            sortino_ratio=_f(r.sortino_ratio),
            profit_factor=_f(r.profit_factor),
            avg_pnl_per_trade=_f(r.avg_pnl_per_trade),
            max_losing_streak=r.max_losing_streak,
            profitable_days_pct=_f(r.profitable_days_pct),
            avg_trades_per_day=_f(r.avg_trades_per_day),
            daily_pnl_std_dev=_f(r.daily_pnl_std_dev),
            long_ratio_pct=_f(r.long_ratio_pct),
            avg_position_size_usd=_f(r.avg_position_size_usd),
            fees_paid_usd=_f(r.fees_paid_usd),
            calmar_ratio=_f(r.calmar_ratio),
            composite_score=_f(r.composite_score),
            max_drawdown_duration_days=_f(r.max_drawdown_duration_days),
            active_trading_days=r.active_trading_days,
            avg_leverage=_f(r.avg_leverage),
        )
        for r in rows
    ]


_PERIOD_MS: dict[str, int | None] = {
    "day": 24 * 60 * 60 * 1000,
    "week": 7 * 24 * 60 * 60 * 1000,
    "month": 30 * 24 * 60 * 60 * 1000,
    "allTime": None,
}


async def fetch_trader_fills(address: str) -> list[Fill]:
    """Fetch recent fills for a trader from the mainnet HL API."""
    client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
    return await client.get_fills(address, limit=None)


async def fetch_trader_export_fills(address: str) -> list[Fill]:
    """Fetch all trader fills currently available through Hyperliquid history."""
    client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
    return await client.get_fills_by_time(address)


def _realized_pnl_for_period(fills: list[Fill], period: str) -> float:
    """Sum closed_pnl of perp fills that fall within the given period window."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_ms = _PERIOD_MS.get(period)
    cutoff_ms = now_ms - window_ms if window_ms is not None else None
    return sum(
        float(f.closed_pnl)
        for f in fills
        if _is_perp_fill(f) and (cutoff_ms is None or f.time >= cutoff_ms)
    )


def _perp_volume_for_period(fills: list[Fill], period: str) -> float:
    """Sum traded notional (px*sz) of perp fills within the period window."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_ms = _PERIOD_MS.get(period)
    cutoff_ms = now_ms - window_ms if window_ms is not None else None
    return sum(
        float(f.px * f.sz)
        for f in fills
        if _is_perp_fill(f) and (cutoff_ms is None or f.time >= cutoff_ms)
    )


# Periods written to trader_stats; keep in sync with refresh_leaderboard.
_STAT_PERIODS: tuple[str, ...] = ("day", "week", "month", "allTime")


def _build_equity_curve_from_fills(fills: list[Fill], period: str) -> list[EquityPoint]:
    """Build cumulative realized-PnL curve from a pre-fetched fill list."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_ms = _PERIOD_MS.get(period)
    cutoff_ms = now_ms - window_ms if window_ms is not None else None

    filtered = sorted(
        (
            f
            for f in fills
            if _is_perp_fill(f) and (cutoff_ms is None or f.time >= cutoff_ms)
        ),
        key=lambda f: f.time,
    )

    cumulative = 0.0
    by_second: dict[int, float] = {}
    for fill in filtered:
        pnl_delta = float(fill.closed_pnl)
        if pnl_delta == 0.0:
            continue
        cumulative += pnl_delta
        by_second[fill.time // 1000] = cumulative

    return [
        EquityPoint(
            ts=datetime.fromtimestamp(sec, tz=UTC).replace(tzinfo=None),
            pnl=pnl,
        )
        for sec, pnl in sorted(by_second.items())
    ]


async def get_equity_curve(
    address: str, period: str, fills: list[Fill] | None = None
) -> list[EquityPoint]:
    """Cumulative closed-PnL curve for the selected time window.

    Built from HL fills; timestamps deduplicated to UTC-second resolution.
    Pass pre-fetched fills to avoid a redundant API call.
    """
    if fills is None:
        client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
        fills = await client.get_fills(address, limit=None)
    return _build_equity_curve_from_fills(fills, period)


def _redis_avg_leverage(address: str) -> float | None:
    """Average leverage from current Redis position snapshot."""
    r = get_redis_client()
    raw: str | None = r.get(f"hl:snapshot:{address}")
    if not raw:
        return None
    positions = _position_adapter.validate_json(raw)
    leverages = [
        p.leverage.value
        for p in positions
        if p.szi != Decimal("0") and p.leverage.value > 0
    ]
    return sum(leverages) / len(leverages) if leverages else None


async def get_open_positions(address: str) -> list[PositionItem]:
    """Latest positions from Redis snapshot (updated every 5s by hl_tracker)."""
    r = get_redis_client()
    raw: str | None = await asyncio.to_thread(r.get, f"hl:snapshot:{address}")

    if raw:
        positions = _position_adapter.validate_json(raw)
    else:
        # Fallback: fetch directly from HL (trader not tracked or snapshot expired)
        client = HyperliquidInfoClient()
        positions = await client.get_positions(address)

    return [
        PositionItem(
            coin=p.coin,
            side=p.side,
            size=float(p.abs_size),
            entry_px=float(p.entry_px) if p.entry_px is not None else None,
            unrealized_pnl=float(p.unrealized_pnl),
            leverage=p.leverage.value,
        )
        for p in positions
        if p.szi != Decimal("0")
    ]


def compute_sharpe_sortino(equity_curve: list[EquityPoint]) -> tuple[float, float]:
    """Compute annualized Sharpe and Sortino ratios from a cumulative PnL curve.

    Buckets points by calendar day (last value per day), derives daily PnL
    changes, then applies the standard formulas with rf_rate = 0.
    Returns (0.0, 0.0) when there is insufficient data.
    """
    if len(equity_curve) < 3:
        return 0.0, 0.0

    daily: dict[str, float] = {}
    for point in equity_curve:
        daily[point.ts.date().isoformat()] = point.pnl

    sorted_values = [v for _, v in sorted(daily.items())]
    if len(sorted_values) < 3:
        return 0.0, 0.0

    n = len(sorted_values)
    changes = [sorted_values[i] - sorted_values[i - 1] for i in range(1, n)]

    if len(changes) < 2:
        return 0.0, 0.0

    mean_ret = statistics.mean(changes)
    std_ret = statistics.stdev(changes)
    if std_ret == 0.0:
        return 0.0, 0.0

    ann = math.sqrt(365)
    sharpe = round(mean_ret / std_ret * ann, 4)

    downside = [r for r in changes if r < 0]
    if not downside:
        return sharpe, sharpe

    down_std = abs(downside[0]) if len(downside) == 1 else statistics.stdev(downside)
    sortino = round(mean_ret / down_std * ann, 4) if down_std > 0.0 else 0.0
    return sharpe, sortino


def get_max_drawdown(equity_curve: list[EquityPoint]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0].pnl
    max_dd = 0.0
    for point in equity_curve:
        if point.pnl > peak:
            peak = point.pnl
        drawdown = peak - point.pnl
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd


async def get_fills(address: str, limit: int = 50) -> list[FillItem]:
    """Fetch recent fills directly from Hyperliquid info API."""
    client = HyperliquidInfoClient()
    fills = await client.get_fills(address, limit=limit)
    return [
        FillItem(
            coin=f.coin,
            side=f.side,
            px=float(f.px),
            sz=float(f.sz),
            dir=f.dir,
            closed_pnl=float(f.closed_pnl),
            time=f.time,
        )
        for f in fills
    ]


async def get_closed_trades(
    address: str, limit: int = 20, fills: list[Fill] | None = None
) -> list[ClosedTradeItem]:
    """Aggregate close fills by oid — one row per closed order.

    Pass pre-fetched fills to avoid a redundant API call.
    """
    if fills is None:
        client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
        fills = await client.get_fills(address, limit=None)

    by_oid: dict[int, list[Fill]] = defaultdict(list)
    for f in fills:
        if f.dir.startswith("Close"):
            by_oid[f.oid].append(f)

    trades: list[ClosedTradeItem] = []
    for group in by_oid.values():
        total_sz: Decimal = sum((f.sz for f in group), Decimal("0"))
        total_pnl: Decimal = sum((f.closed_pnl for f in group), Decimal("0"))
        weighted = sum((f.sz * f.px for f in group), Decimal("0"))
        avg_px = float(weighted / total_sz) if total_sz else 0.0
        direction = "long" if "Long" in group[0].dir else "short"
        trades.append(
            ClosedTradeItem(
                coin=group[0].coin,
                direction=direction,
                size=float(total_sz),
                avg_px=avg_px,
                pnl=float(total_pnl),
                time=min(f.time for f in group),
                fill_count=len(group),
            )
        )

    trades.sort(key=lambda t: t.time, reverse=True)
    return trades[:limit]


# ── Composite score helpers ────────────────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _roi_score(roi: float) -> float:
    if roi <= 0:
        return 0.0
    if roi <= 10:
        return roi * 5
    if roi <= 50:
        return 50.0 + (roi - 10) * 1.25
    return max(0.0, 100.0 - (roi - 50) * 2)


def _risk_score(sharpe: float, sortino: float, mdd_pct: float) -> float:
    s = _clamp(sharpe / 3 * 50, 0, 50)
    so = _clamp(sortino / 4 * 30, 0, 30)
    dd = _clamp((20 - mdd_pct) / 20 * 20, 0, 20)
    return _clamp(s + so + dd, 0, 100)


def _consistency_score(
    pf: float, profitable_days_pct: float, max_losing_streak: int
) -> float:
    pf_s = _clamp((pf - 1) / 1.5 * 50, 0, 50)
    pd_s = _clamp((profitable_days_pct - 50) / 30 * 30, 0, 30)
    ls_s = _clamp((5 - max_losing_streak) / 5 * 20, 0, 20)
    return _clamp(pf_s + pd_s + ls_s, 0, 100)


def _win_rate_score(wr: float) -> float:
    if wr < 50:
        return max(0.0, (wr - 30) / 20 * 50)
    if wr <= 70:
        return 50.0 + (wr - 50) / 20 * 50
    return max(0.0, 100.0 - (wr - 70) / 10 * 40)


def _activity_score(tpd: float) -> float:
    if tpd <= 0:
        return 0.0
    if tpd <= 1:
        return tpd * 50
    if tpd <= 10:
        return 50.0 + (tpd - 1) / 9 * 40
    if tpd <= 20:
        return max(60.0, 90.0 - (tpd - 10) * 3)
    return max(0.0, 60.0 - (tpd - 20) * 3)


def _sharpe_score(sharpe: float, sortino: float) -> float:
    return _clamp(sharpe / 3 * 70 + sortino / 4 * 30, 0, 100)


def compute_composite_score(
    roi_30d_pct: float | None,
    win_rate_pct: float | None,
    sharpe_ratio: float | None,
    sortino_ratio: float | None,
    max_drawdown_pct: float | None,
    profit_factor: float | None,
    profitable_days_pct: float | None,
    max_losing_streak: int | None,
    avg_trades_per_day: float | None,
) -> float | None:
    """Compute composite score (0–100) from trader quality metrics.

    Returns None when essential inputs are unavailable.
    Passing score: ≥ 70.
    """
    if roi_30d_pct is None or win_rate_pct is None:
        return None

    # Neutral substitutes for missing data: chosen so each unknown component
    # contributes 0 bonus points rather than the best or worst possible value.
    # mdd_pct=50 → dd=0; pf=1.0 → pf_s=0; pdp=50 → pd_s=0; mls=5 → ls_s=0.
    score = (
        0.25 * _roi_score(roi_30d_pct)
        + 0.20
        * _risk_score(
            sharpe_ratio if sharpe_ratio is not None else 0.0,
            sortino_ratio if sortino_ratio is not None else 0.0,
            max_drawdown_pct if max_drawdown_pct is not None else 50.0,
        )
        + 0.20
        * _consistency_score(
            profit_factor if profit_factor is not None else 1.0,
            profitable_days_pct if profitable_days_pct is not None else 50.0,
            max_losing_streak if max_losing_streak is not None else 5,
        )
        + 0.15 * _win_rate_score(win_rate_pct)
        + 0.10
        * _activity_score(avg_trades_per_day if avg_trades_per_day is not None else 0.0)
        + 0.10
        * _sharpe_score(
            sharpe_ratio if sharpe_ratio is not None else 0.0,
            sortino_ratio if sortino_ratio is not None else 0.0,
        )
    )
    return round(_clamp(score, 0, 100), 2)


# ── Quality metrics helpers ────────────────────────────────────────────────────


def _max_losing_streak(pnls: list[float]) -> int:
    """Count the longest consecutive sequence of losing (negative) trades."""
    max_streak = current = 0
    for p in pnls:
        if p < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _max_drawdown_duration_days(equity_curve: list[EquityPoint]) -> float:
    """Return the longest calendar duration (days) between a peak and full recovery."""
    if len(equity_curve) < 2:
        return 0.0

    peak_pnl = equity_curve[0].pnl
    peak_ts = equity_curve[0].ts
    in_drawdown = False
    dd_start_ts: datetime | None = None
    max_duration = 0.0

    for point in equity_curve:
        if point.pnl > peak_pnl:
            if in_drawdown and dd_start_ts is not None:
                duration = (point.ts - dd_start_ts).total_seconds() / 86400
                max_duration = max(max_duration, duration)
            peak_pnl = point.pnl
            peak_ts = point.ts
            in_drawdown = False
            dd_start_ts = None
        elif point.pnl < peak_pnl and not in_drawdown:
            in_drawdown = True
            dd_start_ts = peak_ts

    # Still in drawdown at end of series
    if in_drawdown and dd_start_ts is not None:
        duration = (equity_curve[-1].ts - dd_start_ts).total_seconds() / 86400
        max_duration = max(max_duration, duration)

    return round(max_duration, 2)


class _TradeMetrics(NamedTuple):
    trade_count: int | None
    win_rate_pct: float | None
    avg_trade_duration_hrs: float | None
    profit_factor: float | None
    avg_pnl_per_trade: float | None
    max_losing_streak: int | None


class _DailyMetrics(NamedTuple):
    profitable_days_pct: float | None
    avg_trades_per_day: float | None
    daily_pnl_std_dev: float | None
    active_trading_days: int | None


class _RiskMetrics(NamedTuple):
    max_drawdown_usd: float | None
    max_drawdown_pct: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    max_drawdown_duration_days: float | None


def _compute_per_trade_metrics(
    closing_by_oid: dict[int, list[Fill]],
    open_fills_by_coin: dict[str, list[Fill]],
) -> _TradeMetrics:
    if not closing_by_oid:
        return _TradeMetrics(None, None, None, None, None, None)

    trade_count = len(closing_by_oid)
    trade_pnls: list[float] = []
    durations_hrs: list[float] = []

    for close_fills in closing_by_oid.values():
        pnl = sum(float(f.closed_pnl) for f in close_fills)
        trade_pnls.append(pnl)

        coin = close_fills[0].coin
        close_min_time = min(f.time for f in close_fills)
        open_candidates = [
            f for f in open_fills_by_coin.get(coin, []) if f.time < close_min_time
        ]
        if open_candidates:
            open_time = max(f.time for f in open_candidates)
            durations_hrs.append((close_min_time - open_time) / 1000 / 3600)

    winning = sum(1 for p in trade_pnls if p > 0)
    winners = [p for p in trade_pnls if p > 0]
    losers = [abs(p) for p in trade_pnls if p < 0]

    return _TradeMetrics(
        trade_count=trade_count,
        win_rate_pct=(winning / trade_count) * 100,
        avg_trade_duration_hrs=(
            sum(durations_hrs) / len(durations_hrs) if durations_hrs else None
        ),
        profit_factor=sum(winners) / sum(losers) if losers else None,
        avg_pnl_per_trade=sum(trade_pnls) / len(trade_pnls),
        max_losing_streak=_max_losing_streak(trade_pnls),
    )


def _compute_daily_metrics(
    all_fills: list[Fill],
    closing_by_oid: dict[int, list[Fill]],
    trade_count: int | None,
) -> _DailyMetrics:
    daily_pnl_map: dict[str, float] = defaultdict(float)
    for f in all_fills:
        day = datetime.fromtimestamp(f.time / 1000, tz=UTC).date().isoformat()
        daily_pnl_map[day] += float(f.closed_pnl)

    profitable_days_pct: float | None = (
        sum(1 for v in daily_pnl_map.values() if v > 0) / len(daily_pnl_map) * 100
        if daily_pnl_map
        else None
    )

    close_day_set: set[str] = {
        datetime.fromtimestamp(min(f.time for f in fills_list) / 1000, tz=UTC)
        .date()
        .isoformat()
        for fills_list in closing_by_oid.values()
    }
    active_days = len(close_day_set)
    avg_trades_per_day: float | None = (
        trade_count / active_days if active_days > 0 and trade_count else None
    )

    daily_vals = list(daily_pnl_map.values())
    daily_pnl_std_dev: float | None = (
        statistics.stdev(daily_vals) if len(daily_vals) >= 2 else None
    )

    return _DailyMetrics(
        profitable_days_pct=profitable_days_pct,
        avg_trades_per_day=avg_trades_per_day,
        daily_pnl_std_dev=daily_pnl_std_dev,
        active_trading_days=active_days if active_days > 0 else None,
    )


def _compute_risk_metrics(equity_curve: list[EquityPoint]) -> _RiskMetrics:
    if not equity_curve:
        return _RiskMetrics(None, None, None, None, None, None)

    max_dd_usd = get_max_drawdown(equity_curve)
    peak_pnl = max(p.pnl for p in equity_curve)
    if peak_pnl > 0:
        max_dd_pct: float = min((max_dd_usd / peak_pnl) * 100, 999.99)
    elif max_dd_usd > 0:
        max_dd_pct = 999.99
    else:
        max_dd_pct = 0.0

    sharpe_raw, sortino_raw = compute_sharpe_sortino(equity_curve)
    alltime_pnl = equity_curve[-1].pnl
    dd_duration = _max_drawdown_duration_days(equity_curve)

    return _RiskMetrics(
        max_drawdown_usd=max_dd_usd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe_raw if sharpe_raw != 0.0 else None,
        sortino_ratio=sortino_raw if sortino_raw != 0.0 else None,
        calmar_ratio=(
            alltime_pnl / max_dd_usd if max_dd_usd and max_dd_usd > 0 else None
        ),
        max_drawdown_duration_days=dd_duration if dd_duration > 0 else None,
    )


class QualityMetrics:
    """Container for computed trader quality metrics."""

    def __init__(
        self,
        win_rate_pct: float | None,
        max_drawdown_usd: float | None,
        max_drawdown_pct: float | None,
        trade_count: int | None,
        avg_trade_duration_hrs: float | None,
        first_trade_at: datetime | None,
        sharpe_ratio: float | None,
        sortino_ratio: float | None,
        profit_factor: float | None,
        avg_pnl_per_trade: float | None,
        max_losing_streak: int | None,
        profitable_days_pct: float | None,
        avg_trades_per_day: float | None,
        daily_pnl_std_dev: float | None,
        long_ratio_pct: float | None,
        avg_position_size_usd: float | None,
        fees_paid_usd: float | None,
        calmar_ratio: float | None,
        max_drawdown_duration_days: float | None,
        active_trading_days: int | None,
        avg_leverage: float | None,
        composite_score: float | None = None,
        has_perp_activity: bool = True,
        perp_period_stats: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.win_rate_pct = win_rate_pct
        self.max_drawdown_usd = max_drawdown_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.trade_count = trade_count
        self.avg_trade_duration_hrs = avg_trade_duration_hrs
        self.first_trade_at = first_trade_at
        self.sharpe_ratio = sharpe_ratio
        self.sortino_ratio = sortino_ratio
        self.profit_factor = profit_factor
        self.avg_pnl_per_trade = avg_pnl_per_trade
        self.max_losing_streak = max_losing_streak
        self.profitable_days_pct = profitable_days_pct
        self.avg_trades_per_day = avg_trades_per_day
        self.daily_pnl_std_dev = daily_pnl_std_dev
        self.long_ratio_pct = long_ratio_pct
        self.avg_position_size_usd = avg_position_size_usd
        self.fees_paid_usd = fees_paid_usd
        self.calmar_ratio = calmar_ratio
        self.max_drawdown_duration_days = max_drawdown_duration_days
        self.active_trading_days = active_trading_days
        self.avg_leverage = avg_leverage
        self.composite_score = composite_score
        # Not part of to_dict(): this lives on the Trader row, not trader_stats.
        self.has_perp_activity = has_perp_activity
        # Not part of to_dict(): per-period (pnl_usd, volume_usd) written
        # separately because each period gets distinct values.
        self.perp_period_stats = perp_period_stats

    def to_dict(self) -> dict[str, object]:
        return {
            "win_rate_pct": self.win_rate_pct,
            "max_drawdown_usd": self.max_drawdown_usd,
            "max_drawdown_pct": self.max_drawdown_pct,
            "trade_count": self.trade_count,
            "avg_trade_duration_hrs": self.avg_trade_duration_hrs,
            "first_trade_at": self.first_trade_at,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "profit_factor": self.profit_factor,
            "avg_pnl_per_trade": self.avg_pnl_per_trade,
            "max_losing_streak": self.max_losing_streak,
            "profitable_days_pct": self.profitable_days_pct,
            "avg_trades_per_day": self.avg_trades_per_day,
            "daily_pnl_std_dev": self.daily_pnl_std_dev,
            "long_ratio_pct": self.long_ratio_pct,
            "avg_position_size_usd": self.avg_position_size_usd,
            "fees_paid_usd": self.fees_paid_usd,
            "calmar_ratio": self.calmar_ratio,
            "max_drawdown_duration_days": self.max_drawdown_duration_days,
            "active_trading_days": self.active_trading_days,
            "avg_leverage": self.avg_leverage,
            "composite_score": self.composite_score,
        }


async def compute_trader_quality_metrics(address: str) -> QualityMetrics | None:
    """Compute quality metrics for a trader from their fill history.

    Makes a single HTTP request to HL API.
    Returns None if no fills are available.
    """
    client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
    all_fills, avg_leverage = await asyncio.gather(
        client.get_fills(address, limit=None),
        asyncio.to_thread(_redis_avg_leverage, address),
    )

    if not all_fills:
        return None

    # All analytics are computed strictly on perp fills — the copy engine mirrors
    # only perps, so spot ("Buy"/"Sell") and prediction-market
    # ("Negate/Split/Merge Outcome") fills must not skew win rate, PnL, equity
    # curve, drawdown, fees, etc. A trader with no perp fill is not copyable and is
    # flagged for exclusion from the listing. See _is_perp_fill.
    perp_fills = [f for f in all_fills if _is_perp_fill(f)]
    has_perp_activity = bool(perp_fills)

    # Per-period perp realized PnL + traded volume — these replace the leaderboard
    # (all-markets) pnl_usd/volume_usd on the listing cards.
    perp_period_stats = {
        p: (
            _realized_pnl_for_period(perp_fills, p),
            _perp_volume_for_period(perp_fills, p),
        )
        for p in _STAT_PERIODS
    }

    first_trade_at: datetime | None = None
    if perp_fills:
        first_trade_at = datetime.fromtimestamp(
            min(f.time for f in perp_fills) / 1000, tz=UTC
        ).replace(tzinfo=None)

    # Group fills by direction
    closing_by_oid: dict[int, list[Fill]] = defaultdict(list)
    open_fills_by_coin: dict[str, list[Fill]] = defaultdict(list)
    for f in perp_fills:
        if f.dir.startswith("Close"):
            closing_by_oid[f.oid].append(f)
        elif f.dir.startswith("Open"):
            open_fills_by_coin[f.coin].append(f)
    for coin_fills in open_fills_by_coin.values():
        coin_fills.sort(key=lambda f: f.time)

    trade = _compute_per_trade_metrics(closing_by_oid, open_fills_by_coin)
    daily = _compute_daily_metrics(perp_fills, closing_by_oid, trade.trade_count)

    # Fill-level aggregates (use only opening fills to measure initiated position size)
    open_fills_flat = [f for f in perp_fills if f.dir.startswith("Open")]
    open_long_count = sum(1 for f in open_fills_flat if "Long" in f.dir)
    long_ratio_pct: float | None = (
        open_long_count / len(open_fills_flat) * 100 if open_fills_flat else None
    )
    avg_position_size_usd: float | None = (
        sum(float(f.px * f.sz) for f in open_fills_flat) / len(open_fills_flat)
        if open_fills_flat
        else None
    )
    # Store 0.0 when the API returns no fee data so the column is not misleadingly NULL.
    fees_paid_usd: float | None = sum(float(f.fee) for f in perp_fills)

    equity_curve = _build_equity_curve_from_fills(perp_fills, "allTime")
    risk = _compute_risk_metrics(equity_curve)

    return QualityMetrics(
        win_rate_pct=trade.win_rate_pct,
        max_drawdown_usd=risk.max_drawdown_usd,
        max_drawdown_pct=risk.max_drawdown_pct,
        trade_count=trade.trade_count,
        avg_trade_duration_hrs=trade.avg_trade_duration_hrs,
        first_trade_at=first_trade_at,
        sharpe_ratio=risk.sharpe_ratio,
        sortino_ratio=risk.sortino_ratio,
        profit_factor=trade.profit_factor,
        avg_pnl_per_trade=trade.avg_pnl_per_trade,
        max_losing_streak=trade.max_losing_streak,
        profitable_days_pct=daily.profitable_days_pct,
        avg_trades_per_day=daily.avg_trades_per_day,
        daily_pnl_std_dev=daily.daily_pnl_std_dev,
        long_ratio_pct=long_ratio_pct,
        avg_position_size_usd=avg_position_size_usd,
        fees_paid_usd=fees_paid_usd,
        calmar_ratio=risk.calmar_ratio,
        max_drawdown_duration_days=risk.max_drawdown_duration_days,
        active_trading_days=daily.active_trading_days,
        avg_leverage=avg_leverage,
        has_perp_activity=has_perp_activity,
        perp_period_stats=perp_period_stats,
    )


async def get_trader_by_id(db: AsyncSession, trader_id: int) -> Trader | None:
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    return result.scalar_one_or_none()

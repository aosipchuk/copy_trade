import asyncio
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clickhouse_client import get_ch_client
from app.core.config import settings
from app.core.logging import get_logger
from app.models.trader import Trader, TraderStat
from app.schemas.trader import (
    ClosedTradeItem,
    EquityPoint,
    FillItem,
    PositionItem,
    TraderStatSchema,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import Fill

logger = get_logger(__name__)


def _f(val: object) -> float | None:
    """Cast a Numeric/Decimal column value to float, or return None."""
    return float(val) if val is not None else None  # type: ignore[arg-type]


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
    """Fetch the most recent fills for a trader from the mainnet HL API."""
    client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
    return await client.get_fills(address, limit=None)


def _realized_pnl_for_period(fills: list[Fill], period: str) -> float:
    """Sum closed_pnl of fills that fall within the given period window."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_ms = _PERIOD_MS.get(period)
    cutoff_ms = now_ms - window_ms if window_ms is not None else None
    return sum(
        float(f.closed_pnl) for f in fills if cutoff_ms is None or f.time >= cutoff_ms
    )


def _build_equity_curve_from_fills(fills: list[Fill], period: str) -> list[EquityPoint]:
    """Build cumulative realized-PnL curve from a pre-fetched fill list."""
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_ms = _PERIOD_MS.get(period)
    cutoff_ms = now_ms - window_ms if window_ms is not None else None

    filtered = sorted(
        (f for f in fills if cutoff_ms is None or f.time >= cutoff_ms),
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


def _ch_open_positions(
    address: str,
) -> list[tuple[str, str, float, float, float, float]]:  # noqa: E501
    ch = get_ch_client()
    rows = ch.execute(
        """
        SELECT
            coin,
            argMax(side, snapshot_at)        AS side,
            argMax(szi, snapshot_at)         AS szi,
            argMax(entry_px, snapshot_at)    AS entry_px,
            argMax(unrealized_pnl, snapshot_at) AS unrealized_pnl,
            argMax(leverage, snapshot_at)    AS leverage
        FROM copytrade.trader_positions
        WHERE trader_address = %(addr)s
          AND snapshot_at >= now() - INTERVAL 5 MINUTE
        GROUP BY coin
        HAVING szi != 0
        ORDER BY coin
        """,
        {"addr": address},
    )
    return [
        (r[0], r[1], float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows
    ]  # noqa: E501


async def get_open_positions(address: str) -> list[PositionItem]:
    """Return latest open positions from ClickHouse snapshot."""
    rows = await asyncio.to_thread(_ch_open_positions, address)
    return [
        PositionItem(
            coin=coin,
            side=side,
            size=abs(szi),
            entry_px=entry_px if entry_px != 0.0 else None,
            unrealized_pnl=upnl,
            leverage=int(leverage),
        )
        for coin, side, szi, entry_px, upnl, leverage in rows
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
        composite_score: float | None = None,
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
        self.composite_score = composite_score

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
            "composite_score": self.composite_score,
        }


async def compute_trader_quality_metrics(address: str) -> QualityMetrics | None:
    """Compute quality metrics for a trader from their fill history.

    Makes a single HTTP request to HL API.
    Returns None if no fills are available.
    """
    client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
    all_fills = await client.get_fills(address, limit=None)

    if not all_fills:
        return None

    all_fills_sorted = sorted(all_fills, key=lambda f: f.time)
    first_trade_at = datetime.fromtimestamp(
        all_fills_sorted[0].time / 1000, tz=UTC
    ).replace(tzinfo=None)

    # ── Group fills by direction ───────────────────────────────────────────────
    closing_by_oid: dict[int, list[Fill]] = defaultdict(list)
    open_fills_by_coin: dict[str, list[Fill]] = defaultdict(list)
    for f in all_fills:
        if f.dir.startswith("Close"):
            closing_by_oid[f.oid].append(f)
        elif f.dir.startswith("Open"):
            open_fills_by_coin[f.coin].append(f)

    for coin_fills in open_fills_by_coin.values():
        coin_fills.sort(key=lambda f: f.time)

    # ── Per-trade metrics ─────────────────────────────────────────────────────
    trade_count: int | None = None
    win_rate_pct: float | None = None
    avg_trade_duration_hrs: float | None = None
    profit_factor: float | None = None
    avg_pnl_per_trade: float | None = None
    max_losing_streak_val: int | None = None

    if closing_by_oid:
        trade_count = len(closing_by_oid)
        trade_pnls: list[float] = []
        durations_hrs: list[float] = []

        for close_fills in closing_by_oid.values():
            pnl = sum(float(f.closed_pnl) for f in close_fills)
            trade_pnls.append(pnl)

            # Correct hold-time: find the most recent open fill for this coin
            # that occurred before the first fill of this close group.
            coin = close_fills[0].coin
            close_min_time = min(f.time for f in close_fills)
            open_candidates = [
                f for f in open_fills_by_coin.get(coin, []) if f.time < close_min_time
            ]
            if open_candidates:
                open_time = max(f.time for f in open_candidates)
                durations_hrs.append((close_min_time - open_time) / 1000 / 3600)

        winning = sum(1 for p in trade_pnls if p > 0)
        win_rate_pct = (winning / trade_count) * 100

        winners = [p for p in trade_pnls if p > 0]
        losers = [abs(p) for p in trade_pnls if p < 0]
        profit_factor = sum(winners) / sum(losers) if losers else None
        avg_pnl_per_trade = sum(trade_pnls) / len(trade_pnls)
        max_losing_streak_val = _max_losing_streak(trade_pnls)

        if durations_hrs:
            avg_trade_duration_hrs = sum(durations_hrs) / len(durations_hrs)

    # ── Daily aggregates ──────────────────────────────────────────────────────
    daily_pnl_map: dict[str, float] = defaultdict(float)
    for f in all_fills:
        day = datetime.fromtimestamp(f.time / 1000, tz=UTC).date().isoformat()
        daily_pnl_map[day] += float(f.closed_pnl)

    profitable_days_pct: float | None = None
    avg_trades_per_day: float | None = None
    daily_pnl_std_dev: float | None = None

    if daily_pnl_map:
        profitable_days_pct = (
            sum(1 for v in daily_pnl_map.values() if v > 0) / len(daily_pnl_map) * 100
        )

    # active_days = distinct calendar days on which a trade was *closed*,
    # matching the denominator of trade_count (closing fills only).
    close_day_set: set[str] = {
        datetime.fromtimestamp(min(f.time for f in fills_list) / 1000, tz=UTC)
        .date()
        .isoformat()
        for fills_list in closing_by_oid.values()
    }
    active_days = len(close_day_set)
    if active_days > 0 and trade_count:
        avg_trades_per_day = trade_count / active_days

    daily_vals = list(daily_pnl_map.values())
    if len(daily_vals) >= 2:
        daily_pnl_std_dev = statistics.stdev(daily_vals)

    # ── Fill-level aggregates ─────────────────────────────────────────────────
    open_fills_flat = [f for f in all_fills if f.dir.startswith("Open")]
    open_long_count = sum(1 for f in open_fills_flat if "Long" in f.dir)
    long_ratio_pct: float | None = (
        open_long_count / len(open_fills_flat) * 100 if open_fills_flat else None
    )
    # Use only opening fills so we measure the size of initiated positions,
    # not the closing legs (which would halve the average for each round-trip).
    avg_position_size_usd: float | None = (
        sum(float(f.px * f.sz) for f in open_fills_flat) / len(open_fills_flat)
        if open_fills_flat
        else None
    )
    # Store 0.0 when the API returns no fee data (field defaults to 0) so the
    # column is not misleadingly NULL for traders with genuinely zero fees.
    fees_paid_usd: float | None = sum(float(f.fee) for f in all_fills)

    # ── Equity curve, drawdown, Sharpe/Sortino, Calmar ───────────────────────
    equity_curve = _build_equity_curve_from_fills(all_fills, "allTime")
    max_dd_usd: float | None = None
    max_dd_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar_ratio: float | None = None
    max_dd_duration_days: float | None = None

    if equity_curve:
        max_dd_usd = get_max_drawdown(equity_curve)
        peak_pnl = max(p.pnl for p in equity_curve)
        if peak_pnl > 0:
            max_dd_pct = min((max_dd_usd / peak_pnl) * 100, 999.99)
        elif max_dd_usd > 0:
            max_dd_pct = 999.99
        else:
            max_dd_pct = 0.0

        sharpe_raw, sortino_raw = compute_sharpe_sortino(equity_curve)
        sharpe = sharpe_raw if sharpe_raw != 0.0 else None
        sortino = sortino_raw if sortino_raw != 0.0 else None

        alltime_pnl = equity_curve[-1].pnl
        # Non-standard Calmar proxy: USD PnL / USD max-drawdown (not annualised %).
        # Standard Calmar uses annualised_return% / max_drawdown%; filter accordingly.
        calmar_ratio = (
            alltime_pnl / max_dd_usd if max_dd_usd and max_dd_usd > 0 else None
        )

        dd_duration = _max_drawdown_duration_days(equity_curve)
        max_dd_duration_days = dd_duration if dd_duration > 0 else None

    return QualityMetrics(
        win_rate_pct=win_rate_pct,
        max_drawdown_usd=max_dd_usd,
        max_drawdown_pct=max_dd_pct,
        trade_count=trade_count,
        avg_trade_duration_hrs=avg_trade_duration_hrs,
        first_trade_at=first_trade_at,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        profit_factor=profit_factor,
        avg_pnl_per_trade=avg_pnl_per_trade,
        max_losing_streak=max_losing_streak_val,
        profitable_days_pct=profitable_days_pct,
        avg_trades_per_day=avg_trades_per_day,
        daily_pnl_std_dev=daily_pnl_std_dev,
        long_ratio_pct=long_ratio_pct,
        avg_position_size_usd=avg_position_size_usd,
        fees_paid_usd=fees_paid_usd,
        calmar_ratio=calmar_ratio,
        max_drawdown_duration_days=max_dd_duration_days,
    )


async def get_trader_by_id(db: AsyncSession, trader_id: int) -> Trader | None:
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    return result.scalar_one_or_none()

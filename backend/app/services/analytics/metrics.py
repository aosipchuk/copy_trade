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
        float(f.closed_pnl)
        for f in fills
        if cutoff_ms is None or f.time >= cutoff_ms
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
    ) -> None:
        self.win_rate_pct = win_rate_pct
        self.max_drawdown_usd = max_drawdown_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.trade_count = trade_count
        self.avg_trade_duration_hrs = avg_trade_duration_hrs
        self.first_trade_at = first_trade_at
        self.sharpe_ratio = sharpe_ratio
        self.sortino_ratio = sortino_ratio

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
        }


async def compute_trader_quality_metrics(address: str) -> QualityMetrics | None:
    """
    Compute quality metrics for a trader from their fill history.
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

    # Group closing fills by order id
    closing_by_oid: dict[int, list[Fill]] = defaultdict(list)
    for f in all_fills:
        if f.dir.startswith("Close"):
            closing_by_oid[f.oid].append(f)

    trade_count: int | None = None
    win_rate_pct: float | None = None
    avg_trade_duration_hrs: float | None = None

    if closing_by_oid:
        trade_count = len(closing_by_oid)
        winning = sum(
            1
            for fills in closing_by_oid.values()
            if sum(float(f.closed_pnl) for f in fills) > 0
        )
        win_rate_pct = (winning / trade_count) * 100

        # Average time between consecutive trade closures as proxy for holding period
        close_times = sorted(
            min(f.time for f in fills) for fills in closing_by_oid.values()
        )
        if len(close_times) > 1:
            deltas_hrs = [
                (close_times[i + 1] - close_times[i]) / 1000 / 3600
                for i in range(len(close_times) - 1)
            ]
            avg_trade_duration_hrs = sum(deltas_hrs) / len(deltas_hrs)
        else:
            avg_trade_duration_hrs = 0.0

    # Max drawdown and Sharpe/Sortino from allTime equity curve
    equity_curve = _build_equity_curve_from_fills(all_fills, "allTime")
    max_dd_usd: float | None = None
    max_dd_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None

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

    return QualityMetrics(
        win_rate_pct=win_rate_pct,
        max_drawdown_usd=max_dd_usd,
        max_drawdown_pct=max_dd_pct,
        trade_count=trade_count,
        avg_trade_duration_hrs=avg_trade_duration_hrs,
        first_trade_at=first_trade_at,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
    )


async def get_trader_by_id(db: AsyncSession, trader_id: int) -> Trader | None:
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    return result.scalar_one_or_none()

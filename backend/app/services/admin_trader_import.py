from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.trader import Trader, TraderStat
from app.schemas.trader import TraderStatSchema
from app.services.analytics.metrics import (
    QualityMetrics,
    compute_composite_score,
    compute_trader_quality_metrics,
    get_trader_stats,
)
from app.services.hyperliquid.address import normalize_hl_address as _normalize_address

logger = get_logger(__name__)

_STAT_PERIODS: tuple[str, ...] = ("day", "week", "month", "allTime")

ImportStatus = Literal["imported", "refreshed", "no_fills", "no_perp_activity"]


class InvalidHLAddressError(ValueError):
    """Raised when an admin submits an invalid HL wallet address."""


class TraderImportFetchError(RuntimeError):
    """Raised when Hyperliquid data cannot be fetched for an import."""


@dataclass(slots=True)
class AdminTraderImportResult:
    status: ImportStatus
    message: str
    trader: Trader
    stats: list[TraderStatSchema]
    has_perp_activity: bool | None


def normalize_hl_address(address: str) -> str:
    try:
        return _normalize_address(address)
    except ValueError as exc:
        raise InvalidHLAddressError(
            "HL address must be a 42-character 0x-prefixed hex address."
        ) from exc


async def import_hl_trader_for_analysis(
    db: AsyncSession,
    address: str,
) -> AdminTraderImportResult:
    hl_address = normalize_hl_address(address)
    now = datetime.now(UTC).replace(tzinfo=None)

    result = await db.execute(
        select(Trader).where(func.lower(Trader.hl_address) == hl_address)
    )
    trader = result.scalar_one_or_none()
    created = trader is None
    if trader is None:
        trader = Trader(
            hl_address=hl_address,
            display_name=None,
            is_active=False,
            last_seen_at=now,
        )
        db.add(trader)
        await db.flush()
    else:
        trader.last_seen_at = now

    try:
        metrics = await compute_trader_quality_metrics(
            hl_address,
            use_available_history=True,
        )
    except Exception as exc:
        logger.warning(
            "admin_trader_import_fetch_failed",
            trader=hl_address,
            error=str(exc),
        )
        raise TraderImportFetchError(
            "Failed to fetch trader data from Hyperliquid."
        ) from exc

    if metrics is None:
        await db.flush()
        return AdminTraderImportResult(
            status="no_fills",
            message=(
                "Trader was saved for review, but Hyperliquid returned no fills. "
                "Live copy remains disabled until perp activity is confirmed."
            ),
            trader=trader,
            stats=await get_trader_stats(db, trader.id),
            has_perp_activity=trader.has_perp_activity,
        )

    trader.has_perp_activity = metrics.has_perp_activity
    if metrics.has_perp_activity:
        trader.is_active = True

    metrics.composite_score = compute_composite_score(
        roi_30d_pct=None,
        win_rate_pct=metrics.win_rate_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        max_drawdown_pct=metrics.max_drawdown_pct,
        profit_factor=metrics.profit_factor,
        profitable_days_pct=metrics.profitable_days_pct,
        max_losing_streak=metrics.max_losing_streak,
        avg_trades_per_day=metrics.avg_trades_per_day,
    )
    await _upsert_trader_stats(db, trader.id, metrics, now)
    await db.flush()

    if not metrics.has_perp_activity:
        return AdminTraderImportResult(
            status="no_perp_activity",
            message=(
                "Trader was analyzed, but no perp fills were found. "
                "The address is hidden from copyable trader lists."
            ),
            trader=trader,
            stats=await get_trader_stats(db, trader.id),
            has_perp_activity=False,
        )

    return AdminTraderImportResult(
        status="imported" if created else "refreshed",
        message="Trader imported and marked copyable after perp activity check.",
        trader=trader,
        stats=await get_trader_stats(db, trader.id),
        has_perp_activity=True,
    )


async def _upsert_trader_stats(
    db: AsyncSession,
    trader_id: int,
    metrics: QualityMetrics,
    updated_at: datetime,
) -> None:
    metric_values = metrics.to_dict()
    period_stats = metrics.perp_period_stats or {}

    for period in _STAT_PERIODS:
        pnl_usd, volume_usd = period_stats.get(period, (None, None))
        insert_values = {
            "trader_id": trader_id,
            "period": period,
            "roi_pct": None,
            "pnl_usd": pnl_usd,
            "volume_usd": volume_usd,
            "updated_at": updated_at,
            **metric_values,
        }
        update_values = {
            key: value
            for key, value in insert_values.items()
            if key not in {"trader_id", "period", "roi_pct"}
        }
        stmt = (
            pg_insert(TraderStat)
            .values(**insert_values)
            .on_conflict_do_update(
                constraint="trader_stats_pkey",
                set_=update_values,
            )
        )
        await db.execute(stmt)

from sqlalchemy import and_, select

from app.core.database import get_db_session
from app.models.trader import Trader, TraderStat
from app.services.analytics.export_workbook import TraderAllTimeMetricsExportRow


async def fetch_trader_all_time_metrics_export_rows() -> list[
    TraderAllTimeMetricsExportRow
]:
    async with get_db_session() as db:
        result = await db.execute(
            select(
                Trader.hl_address,
                TraderStat.trade_count,
                TraderStat.roi_pct,
                TraderStat.pnl_usd,
                TraderStat.active_trading_days,
                TraderStat.max_drawdown_pct,
            )
            .outerjoin(
                TraderStat,
                and_(
                    TraderStat.trader_id == Trader.id,
                    TraderStat.period == "allTime",
                ),
            )
            .order_by(TraderStat.roi_pct.desc().nulls_last(), Trader.id.desc())
        )
        return [
            TraderAllTimeMetricsExportRow(
                address=address,
                trade_count=trade_count,
                roi_pct=roi_pct,
                pnl_usd=pnl_usd,
                active_trading_days=active_trading_days,
                max_drawdown_pct=max_drawdown_pct,
            )
            for (
                address,
                trade_count,
                roi_pct,
                pnl_usd,
                active_trading_days,
                max_drawdown_pct,
            ) in result.all()
        ]

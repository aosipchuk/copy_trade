import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import Select, and_, or_, select

from app.api.deps import (
    TRADER_EXPORT_TICKET_PREFIX,
    TRADER_EXPORT_TICKET_TTL_SECONDS,
    CurrentUser,
    DBSession,
    get_current_user,
    get_current_user_from_bearer_or_export_ticket,
)
from app.core.cache import cached_json_stale_on_error
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.subscription import Subscription
from app.models.trader import Trader, TraderStat
from app.models.user import User
from app.schemas.trader import (
    ClosedTradeItem,
    EquityPoint,
    FillItem,
    PositionItem,
    TraderDetail,
    TraderListItem,
    TraderListResponse,
    TraderStatSchema,
    TraderSummaryResponse,
    decode_cursor,
    encode_cursor,
)
from app.services.analytics.export_workbook import (
    build_trader_export_workbook,
    trader_export_filename,
    xlsx_media_type,
)
from app.services.analytics.metrics import (
    _realized_pnl_for_period,
    fetch_trader_export_fills,
    fetch_trader_fills,
    get_closed_trades,
    get_equity_curve,
    get_fills,
    get_open_positions,
    get_trader_by_id,
    get_trader_stats,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient

logger = get_logger(__name__)

router = APIRouter(
    prefix="/traders",
    tags=["traders"],
    dependencies=[Depends(get_current_user)],
)
export_router = APIRouter(prefix="/traders", tags=["traders"])
ExportUser = Annotated[User, Depends(get_current_user_from_bearer_or_export_ticket)]

_CACHE_TTL_STATS = 30
_CACHE_TTL_POSITIONS = 5
# Stale-on-error fallback windows: how long the last good value is served when a
# live HL fetch fails (429 / timeout). Long for history, short for positions
# (which go stale fast and would mislead).
_CACHE_TTL_STALE = 3600
_CACHE_TTL_STALE_POSITIONS = 300
_DEFAULT_LIMIT = 50

# Hard gate thresholds. NULL = not yet computed → trader passes through.
# ClickHouse-derived metrics (active_days, avg_trades_per_day) are omitted as gates
# because ClickHouse only has data since system launch — early values are misleading.
_GATE_MIN_TRADES: int = 5
_GATE_MIN_AVG_DURATION_HRS: float = 5 / 60  # 5 min — cuts pure HFT bots
_GATE_MAX_DRAWDOWN_PCT: float = 90.0
_GATE_MIN_SHARPE: float = 0.0
_GATE_MIN_SORTINO: float = 0.0
_GATE_MAX_AVG_LEVERAGE: float = 50.0
_GATE_MIN_WIN_RATE: float = 20.0
_GATE_MAX_WIN_RATE: float = 99.0
_GATE_MIN_PROFIT_FACTOR: float = 0.5
_GATE_MAX_LOSING_STREAK: int = 30
_GATE_MIN_PROFITABLE_DAYS_PCT: float = 20.0
_GATE_MAX_DD_DURATION_DAYS: float = 365.0
_GATE_MIN_COMPOSITE_SCORE: float = 10.0
_GATE_MIN_HUMAN_SCORE: int = 0


def _apply_hard_gates(query: Select) -> Select:  # type: ignore[type-arg]
    """Apply quality gates. NULL = not yet computed → trader passes through."""
    return query.where(
        or_(
            TraderStat.trade_count >= _GATE_MIN_TRADES,
            TraderStat.trade_count.is_(None),
        ),
        or_(
            TraderStat.avg_trade_duration_hrs >= _GATE_MIN_AVG_DURATION_HRS,
            TraderStat.avg_trade_duration_hrs.is_(None),
        ),
        or_(
            TraderStat.max_drawdown_pct <= _GATE_MAX_DRAWDOWN_PCT,
            TraderStat.max_drawdown_pct.is_(None),
        ),
        or_(
            TraderStat.sharpe_ratio >= _GATE_MIN_SHARPE,
            TraderStat.sharpe_ratio.is_(None),
        ),
        or_(
            TraderStat.sortino_ratio >= _GATE_MIN_SORTINO,
            TraderStat.sortino_ratio.is_(None),
        ),
        or_(
            TraderStat.avg_leverage <= _GATE_MAX_AVG_LEVERAGE,
            TraderStat.avg_leverage.is_(None),
        ),
        or_(
            TraderStat.win_rate_pct.between(_GATE_MIN_WIN_RATE, _GATE_MAX_WIN_RATE),
            TraderStat.win_rate_pct.is_(None),
        ),
        or_(
            TraderStat.profit_factor >= _GATE_MIN_PROFIT_FACTOR,
            TraderStat.profit_factor.is_(None),
        ),
        or_(
            TraderStat.max_losing_streak <= _GATE_MAX_LOSING_STREAK,
            TraderStat.max_losing_streak.is_(None),
        ),
        or_(
            TraderStat.profitable_days_pct >= _GATE_MIN_PROFITABLE_DAYS_PCT,
            TraderStat.profitable_days_pct.is_(None),
        ),
        or_(
            TraderStat.max_drawdown_duration_days <= _GATE_MAX_DD_DURATION_DAYS,
            TraderStat.max_drawdown_duration_days.is_(None),
        ),
        or_(
            TraderStat.composite_score >= _GATE_MIN_COMPOSITE_SCORE,
            TraderStat.composite_score.is_(None),
        ),
        or_(
            Trader.human_score >= _GATE_MIN_HUMAN_SCORE,
            Trader.human_score.is_(None),
        ),
        # Only show traders with computed perp activity. This excludes both
        # non-copyable traders (prediction-market / spot-only → False) and those
        # not yet processed by compute_quality_metrics (NULL), whose perp
        # pnl_usd/volume_usd cards would otherwise be empty or stale-leaderboard.
        Trader.has_perp_activity == True,  # noqa: E712
    )


def _f(val: object) -> float | None:
    return float(val) if val is not None else None  # type: ignore[arg-type]


def _make_stat_schema(stat: TraderStat) -> TraderStatSchema:
    return TraderStatSchema(
        period=stat.period,
        pnl_usd=_f(stat.pnl_usd),
        roi_pct=_f(stat.roi_pct),
        volume_usd=_f(stat.volume_usd),
        win_rate_pct=_f(stat.win_rate_pct),
        max_drawdown_usd=_f(stat.max_drawdown_usd),
        max_drawdown_pct=_f(stat.max_drawdown_pct),
        trade_count=stat.trade_count,
        avg_trade_duration_hrs=_f(stat.avg_trade_duration_hrs),
        first_trade_at=stat.first_trade_at,
        sharpe_ratio=_f(stat.sharpe_ratio),
        sortino_ratio=_f(stat.sortino_ratio),
        profit_factor=_f(stat.profit_factor),
        avg_pnl_per_trade=_f(stat.avg_pnl_per_trade),
        max_losing_streak=stat.max_losing_streak,
        profitable_days_pct=_f(stat.profitable_days_pct),
        avg_trades_per_day=_f(stat.avg_trades_per_day),
        daily_pnl_std_dev=_f(stat.daily_pnl_std_dev),
        long_ratio_pct=_f(stat.long_ratio_pct),
        avg_position_size_usd=_f(stat.avg_position_size_usd),
        fees_paid_usd=_f(stat.fees_paid_usd),
        calmar_ratio=_f(stat.calmar_ratio),
        composite_score=_f(stat.composite_score),
        max_drawdown_duration_days=_f(stat.max_drawdown_duration_days),
        active_trading_days=stat.active_trading_days,
        avg_leverage=_f(stat.avg_leverage),
    )


@router.get("", response_model=TraderListResponse)
async def list_traders(
    db: DBSession,
    current_user: CurrentUser,
    period: Literal["day", "week", "month", "allTime"] = "week",
    sort: Literal["roi", "pnl", "volume"] = "roi",
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=200),
    cursor: str | None = None,
    address: str | None = Query(default=None, max_length=100),
    # Quality filters
    min_days: int = Query(default=0, ge=0, le=365),
    min_win_rate: float = Query(default=0, ge=0, le=100),
    max_drawdown: float = Query(default=100, ge=0, le=100),
    min_trades: int = Query(default=0, ge=0),
    min_volume: float = Query(default=0, ge=0),
    quality: bool = Query(default=False),
    subscribed_only: bool = Query(default=False),
    # Scoring filters (Phase 6)
    min_composite_score: float = Query(default=0, ge=0, le=100),
    min_profit_factor: float = Query(default=0, ge=0),
    max_losing_streak: int | None = Query(default=None, ge=0),
    min_profitable_days_pct: float = Query(default=0, ge=0, le=100),
    max_avg_trades_per_day: float | None = Query(default=None, ge=0),
    min_calmar: float = Query(default=0, ge=0),
    min_roi: float = Query(default=0, ge=-100),
) -> TraderListResponse:
    sort_col = {
        "roi": TraderStat.roi_pct,
        "pnl": TraderStat.pnl_usd,
        "volume": TraderStat.volume_usd,
    }[sort]

    query = _apply_hard_gates(
        select(Trader, TraderStat)
        .join(TraderStat, TraderStat.trader_id == Trader.id)
        .where(
            Trader.is_active == True,  # noqa: E712
            TraderStat.period == period,
        )
        .order_by(sort_col.desc().nulls_last(), Trader.id.desc())
        .limit(limit + 1)
    )

    if address:
        query = query.where(Trader.hl_address.ilike(f"%{address}%"))

    # Quality preset: verified traders with solid track record
    if quality:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
        query = query.where(
            TraderStat.first_trade_at <= cutoff,
            TraderStat.win_rate_pct >= 40,
            TraderStat.max_drawdown_pct <= 60,
            TraderStat.trade_count >= 10,
        )

    # User-set filters: NULL means unknown → exclude. Unlike hard gates, explicit
    # user filters should not pass traders whose metrics haven't been computed yet.
    if min_days > 0:
        query = query.where(TraderStat.active_trading_days >= min_days)
    if min_win_rate > 0:
        query = query.where(TraderStat.win_rate_pct >= min_win_rate)
    if max_drawdown < 100:
        query = query.where(TraderStat.max_drawdown_pct <= max_drawdown)
    if min_trades > 0:
        query = query.where(TraderStat.trade_count >= min_trades)
    if min_volume > 0:
        query = query.where(TraderStat.volume_usd >= min_volume)

    if subscribed_only:
        query = query.join(
            Subscription,
            and_(
                Subscription.trader_id == Trader.id,
                Subscription.user_id == current_user.id,
                Subscription.is_active.is_(True),
            ),
        )

    # Scoring-metric filters: NULL composite_score excluded; other NULLs included
    if min_composite_score > 0:
        query = query.where(TraderStat.composite_score >= min_composite_score)
    if min_profit_factor > 0:
        col_pf = TraderStat.profit_factor
        query = query.where(or_(col_pf >= min_profit_factor, col_pf.is_(None)))
    if max_losing_streak is not None:
        col_ls = TraderStat.max_losing_streak
        query = query.where(or_(col_ls <= max_losing_streak, col_ls.is_(None)))
    if min_profitable_days_pct > 0:
        col_pd = TraderStat.profitable_days_pct
        query = query.where(or_(col_pd >= min_profitable_days_pct, col_pd.is_(None)))
    if max_avg_trades_per_day is not None:
        col_tpd = TraderStat.avg_trades_per_day
        query = query.where(or_(col_tpd <= max_avg_trades_per_day, col_tpd.is_(None)))
    if min_calmar > 0:
        col_cal = TraderStat.calmar_ratio
        query = query.where(or_(col_cal >= min_calmar, col_cal.is_(None)))
    if min_roi != 0:
        query = query.where(TraderStat.roi_pct >= min_roi)

    if cursor:
        try:
            cursor_val, cursor_id = decode_cursor(cursor)
        except Exception as cursor_exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
            ) from cursor_exc

        if cursor_val is not None:
            query = query.where(
                or_(
                    sort_col < cursor_val,
                    and_(sort_col == cursor_val, Trader.id < cursor_id),
                )
            )
        else:
            query = query.where(and_(sort_col.is_(None), Trader.id < cursor_id))

    result = await db.execute(query)
    rows = result.all()

    has_next = len(rows) > limit
    rows = rows[:limit]

    items: list[TraderListItem] = []
    for trader, stat in rows:
        items.append(
            TraderListItem(
                id=trader.id,
                hl_address=trader.hl_address,
                display_name=trader.display_name,
                stats=[_make_stat_schema(stat)],
            )
        )

    next_cursor: str | None = None
    if has_next and items:
        last = items[-1]
        last_stat = last.stats[0] if last.stats else None
        if last_stat is None:
            sort_val = None
        elif sort == "roi":
            sort_val = last_stat.roi_pct
        elif sort == "pnl":
            sort_val = last_stat.pnl_usd
        else:
            sort_val = last_stat.volume_usd
        next_cursor = encode_cursor(sort_val, last.id)

    return TraderListResponse(items=items, next_cursor=next_cursor)


@router.get("/{trader_id}", response_model=TraderDetail)
async def get_trader(trader_id: int, db: DBSession) -> TraderDetail:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    stats = await get_trader_stats(db, trader_id)
    return TraderDetail(
        id=trader.id,
        hl_address=trader.hl_address,
        display_name=trader.display_name,
        is_active=trader.is_active,
        last_seen_at=trader.last_seen_at,
        stats=stats,
    )


@router.get("/{trader_id}/equity-curve", response_model=list[EquityPoint])
async def trader_equity_curve(
    trader_id: int,
    db: DBSession,
    period: Literal["day", "week", "month", "allTime"] = "week",
) -> list[EquityPoint]:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    async def _fetch() -> list[dict[str, Any]]:
        points = await get_equity_curve(trader.hl_address, period)
        return [p.model_dump(mode="json") for p in points]

    cache_key = f"analytics:equity:{trader_id}:{period}"
    raw = await cached_json_stale_on_error(
        cache_key, _CACHE_TTL_STATS, _CACHE_TTL_STALE, _fetch
    )
    return [EquityPoint(**r) for r in raw]


@router.get("/{trader_id}/positions", response_model=list[PositionItem])
async def trader_positions(trader_id: int, db: DBSession) -> list[PositionItem]:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    async def _fetch() -> list[dict[str, Any]]:
        client = HyperliquidInfoClient()
        positions = await client.get_positions(trader.hl_address)
        return [
            PositionItem(
                coin=p.coin,
                side=p.side,
                size=float(p.abs_size),
                entry_px=float(p.entry_px) if p.entry_px is not None else None,
                unrealized_pnl=float(p.unrealized_pnl),
                leverage=p.leverage.value,
            ).model_dump()
            for p in positions
        ]

    cache_key = f"analytics:positions:{trader_id}"
    raw = await cached_json_stale_on_error(
        cache_key, _CACHE_TTL_POSITIONS, _CACHE_TTL_STALE_POSITIONS, _fetch
    )
    return [PositionItem(**r) for r in raw]


@router.get("/{trader_id}/fills", response_model=list[FillItem])
async def trader_fills(
    trader_id: int,
    db: DBSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[FillItem]:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    async def _fetch() -> list[dict[str, Any]]:
        fills = await get_fills(trader.hl_address, limit=limit)
        return [f.model_dump() for f in fills]

    cache_key = f"analytics:fills:{trader_id}:{limit}"
    raw = await cached_json_stale_on_error(
        cache_key, _CACHE_TTL_STATS, _CACHE_TTL_STALE, _fetch
    )
    return [FillItem(**r) for r in raw]


@router.get("/{trader_id}/closed-trades", response_model=list[ClosedTradeItem])
async def trader_closed_trades(
    trader_id: int,
    db: DBSession,
    limit: int = Query(default=20, ge=1, le=500),
) -> list[ClosedTradeItem]:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    async def _fetch() -> list[dict[str, Any]]:
        trades = await get_closed_trades(trader.hl_address, limit=limit)
        return [t.model_dump() for t in trades]

    cache_key = f"analytics:closed_trades:{trader_id}:{limit}"
    raw = await cached_json_stale_on_error(
        cache_key, _CACHE_TTL_STATS, _CACHE_TTL_STALE, _fetch
    )
    return [ClosedTradeItem(**r) for r in raw]


@export_router.post("/{trader_id}/export-link")
async def trader_export_link(
    trader_id: int,
    db: DBSession,
    current_user: CurrentUser,
) -> dict[str, int | str]:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": current_user.id, "trader_id": trader_id})
    r = get_redis_client()
    await asyncio.to_thread(
        r.setex,
        f"{TRADER_EXPORT_TICKET_PREFIX}{ticket}",
        TRADER_EXPORT_TICKET_TTL_SECONDS,
        payload,
    )
    return {
        "path": f"/traders/{trader_id}/export.xlsx?ticket={ticket}",
        "expires_in": TRADER_EXPORT_TICKET_TTL_SECONDS,
    }


@export_router.get("/{trader_id}/export.xlsx")
async def trader_export_xlsx(
    trader_id: int,
    db: DBSession,
    current_user: ExportUser,
) -> Response:
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    fills_res, stats_res, positions_res = await asyncio.gather(
        fetch_trader_export_fills(trader.hl_address),
        get_trader_stats(db, trader_id),
        get_open_positions(trader.hl_address),
        return_exceptions=True,
    )

    if isinstance(fills_res, BaseException):
        logger.warning(
            "export_fills_failed",
            trader=trader.hl_address,
            user_id=current_user.id,
            error=str(fills_res),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch trader fills for export",
        ) from fills_res

    stats = stats_res if not isinstance(stats_res, BaseException) else []
    if isinstance(stats_res, BaseException):
        logger.warning(
            "export_stats_failed",
            trader=trader.hl_address,
            user_id=current_user.id,
            error=str(stats_res),
        )

    positions = positions_res if not isinstance(positions_res, BaseException) else []
    if isinstance(positions_res, BaseException):
        logger.warning(
            "export_positions_failed",
            trader=trader.hl_address,
            user_id=current_user.id,
            error=str(positions_res),
        )

    workbook = build_trader_export_workbook(
        display_name=trader.display_name,
        address=trader.hl_address,
        stats=stats,
        open_positions=positions,
        fills=fills_res,
    )
    filename = trader_export_filename(trader.display_name, trader.hl_address)
    content_disposition = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=workbook,
        media_type=xlsx_media_type(),
        headers={
            "Content-Disposition": content_disposition,
            "Cache-Control": "no-store",
        },
    )


_CACHE_TTL_SUMMARY = 60


@router.get("/{trader_id}/summary", response_model=TraderSummaryResponse)
async def trader_summary(trader_id: int, db: DBSession) -> TraderSummaryResponse:
    """Full trader profile in one request: stats (all periods), week equity curve,
    open positions, and last 10 closed trades. Cached for 60 s."""
    trader = await get_trader_by_id(db, trader_id)
    if trader is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trader not found"
        )

    async def _fetch() -> dict[str, Any]:
        fills_res, stats_list, positions_res = await asyncio.gather(
            fetch_trader_fills(trader.hl_address),
            get_trader_stats(db, trader_id),
            get_open_positions(trader.hl_address),
            return_exceptions=True,
        )
        # Fills drive recent_trades + equity_curve. If that live HL call failed
        # (e.g. 429), raise so the stale cache serves the last good summary
        # instead of rendering empty trades — the "0 trades" symptom.
        if isinstance(fills_res, BaseException):
            logger.warning(
                "summary_fills_failed", trader=trader.hl_address, error=str(fills_res)
            )
            raise fills_res
        fills: list[Any] = fills_res
        stats_list_safe: list[Any] = (
            stats_list if not isinstance(stats_list, BaseException) else []
        )
        positions: list[Any] = (
            positions_res if not isinstance(positions_res, BaseException) else []
        )
        if isinstance(positions_res, BaseException):
            logger.warning(
                "summary_positions_failed",
                trader=trader.hl_address,
                error=str(positions_res),
            )

        equity_curve, trades = await asyncio.gather(
            get_equity_curve(trader.hl_address, "week", fills=fills),
            get_closed_trades(trader.hl_address, limit=10, fills=fills),
            return_exceptions=True,
        )
        equity_curve = (
            equity_curve if not isinstance(equity_curve, BaseException) else []
        )
        trades = trades if not isinstance(trades, BaseException) else []

        # Replace leaderboard pnl_usd (includes unrealized) with realized-only
        periods = ("day", "week", "month", "allTime")
        realized = {p: _realized_pnl_for_period(fills, p) for p in periods}
        patched_stats = [
            s.model_copy(update={"pnl_usd": realized.get(s.period, s.pnl_usd)})
            for s in stats_list_safe
        ]

        return {
            "id": trader.id,
            "hl_address": trader.hl_address,
            "display_name": trader.display_name,
            "stats": {s.period: s.model_dump(mode="json") for s in patched_stats},
            "equity_curve_week": [e.model_dump(mode="json") for e in equity_curve],
            "open_positions": [p.model_dump() for p in positions],
            "recent_trades": [t.model_dump() for t in trades],
        }

    cache_key = f"analytics:summary:{trader_id}"
    raw = await cached_json_stale_on_error(
        cache_key, _CACHE_TTL_SUMMARY, _CACHE_TTL_STALE, _fetch
    )
    return TraderSummaryResponse(
        id=raw["id"],
        hl_address=raw["hl_address"],
        display_name=raw["display_name"],
        stats={k: TraderStatSchema(**v) for k, v in raw["stats"].items()},
        equity_curve_week=[EquityPoint(**e) for e in raw["equity_curve_week"]],
        open_positions=[PositionItem(**p) for p in raw["open_positions"]],
        recent_trades=[ClosedTradeItem(**t) for t in raw["recent_trades"]],
    )

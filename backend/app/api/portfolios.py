from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DBSession
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioBacktest,
)
from app.schemas.portfolio import (
    ModelPortfolioAllocationDetailResponse,
    ModelPortfolioAllocationResponse,
    ModelPortfolioDetailResponse,
    ModelPortfolioListItemResponse,
    ModelPortfolioPublishedVersionDetailResponse,
    ModelPortfolioResponse,
    ModelPortfolioVersionResponse,
    PortfolioBacktestResponse,
    PortfolioBacktestSummary,
    PortfolioCurrentVersionSummary,
    PortfolioExplanationResponse,
    PortfolioWeeklyReportResponse,
)
from app.services.portfolio.explanations import (
    build_portfolio_explanations,
    generate_weekly_report,
    get_latest_weekly_report,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


def _float(value: object) -> float:
    return float(value)  # type: ignore[arg-type]


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]


def _source_metrics(allocation: ModelPortfolioAllocation) -> dict[str, Any] | None:
    score_snapshot = allocation.score_snapshot or {}
    source_metrics = score_snapshot.get("source_metrics")
    return source_metrics if isinstance(source_metrics, dict) else None


def _portfolio_score(allocation: ModelPortfolioAllocation) -> float | None:
    score_snapshot = allocation.score_snapshot or {}
    return _optional_float(score_snapshot.get("portfolio_score"))


def _version_summary(
    version: ModelPortfolioVersion,
) -> PortfolioCurrentVersionSummary:
    allocations = list(version.allocations)
    return PortfolioCurrentVersionSummary(
        id=version.id,
        version_no=version.version_no,
        status=version.status,
        valid_from=version.valid_from,
        approved_at=version.approved_at,
        trader_count=len(allocations),
        target_weight_sum_pct=round(
            sum(_float(allocation.target_weight_pct) for allocation in allocations),
            3,
        ),
        summary_json=version.summary_json,
    )


def _backtest_summary(backtest: PortfolioBacktest) -> PortfolioBacktestSummary:
    return PortfolioBacktestSummary(
        id=backtest.id,
        portfolio_version_id=backtest.portfolio_version_id,
        period_days=backtest.period_days,
        initial_equity_usd=_float(backtest.initial_equity_usd),
        total_return_pct=_optional_float(backtest.total_return_pct),
        max_drawdown_pct=_optional_float(backtest.max_drawdown_pct),
        sharpe_ratio=_optional_float(backtest.sharpe_ratio),
        sortino_ratio=_optional_float(backtest.sortino_ratio),
        win_rate_pct=_optional_float(backtest.win_rate_pct),
        assumptions_json=backtest.assumptions_json,
        created_at=backtest.created_at,
    )


async def _current_published_version(
    db: AsyncSession, portfolio_id: int
) -> ModelPortfolioVersion | None:
    result = await db.execute(
        select(ModelPortfolioVersion)
        .options(selectinload(ModelPortfolioVersion.allocations))
        .where(
            ModelPortfolioVersion.portfolio_id == portfolio_id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _latest_backtest(
    db: AsyncSession, version_id: int
) -> PortfolioBacktest | None:
    result = await db.execute(
        select(PortfolioBacktest)
        .where(PortfolioBacktest.portfolio_version_id == version_id)
        .order_by(
            PortfolioBacktest.period_days.desc(),
            PortfolioBacktest.initial_equity_usd.asc(),
            PortfolioBacktest.created_at.desc(),
            PortfolioBacktest.id.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _published_detail(
    db: AsyncSession, portfolio_slug: str
) -> tuple[ModelPortfolio, ModelPortfolioVersion]:
    result = await db.execute(
        select(ModelPortfolio).where(
            ModelPortfolio.slug == portfolio_slug,
            ModelPortfolio.status == "active",
        )
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model portfolio not found.",
        )

    version_result = await db.execute(
        select(ModelPortfolioVersion)
        .options(
            selectinload(ModelPortfolioVersion.allocations).selectinload(
                ModelPortfolioAllocation.trader
            ),
            selectinload(ModelPortfolioVersion.backtests),
        )
        .where(
            ModelPortfolioVersion.portfolio_id == portfolio.id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Published portfolio version not found.",
        )
    return portfolio, version


@router.get("", response_model=list[ModelPortfolioListItemResponse])
async def list_portfolios(
    current_user: CurrentUser,
    db: DBSession,
) -> list[ModelPortfolioListItemResponse]:
    _ = current_user
    result = await db.execute(
        select(ModelPortfolio)
        .where(ModelPortfolio.status == "active")
        .order_by(ModelPortfolio.name.asc(), ModelPortfolio.id.asc())
    )
    portfolios = list(result.scalars().all())
    responses: list[ModelPortfolioListItemResponse] = []

    for portfolio in portfolios:
        current_version = await _current_published_version(db, portfolio.id)
        latest_backtest = (
            await _latest_backtest(db, current_version.id)
            if current_version is not None
            else None
        )
        responses.append(
            ModelPortfolioListItemResponse(
                **ModelPortfolioResponse.model_validate(portfolio).model_dump(),
                current_version=(
                    _version_summary(current_version)
                    if current_version is not None
                    else None
                ),
                latest_backtest=(
                    _backtest_summary(latest_backtest)
                    if latest_backtest is not None
                    else None
                ),
            )
        )

    return responses


@router.get("/{slug}", response_model=ModelPortfolioDetailResponse)
async def get_portfolio(
    slug: str,
    current_user: CurrentUser,
    db: DBSession,
) -> ModelPortfolioDetailResponse:
    _ = current_user
    portfolio, version = await _published_detail(db, slug)
    allocations = [
        ModelPortfolioAllocationDetailResponse(
            **ModelPortfolioAllocationResponse.model_validate(allocation).model_dump(),
            trader_address=allocation.trader.hl_address,
            trader_display_name=allocation.trader.display_name,
            portfolio_score=_portfolio_score(allocation),
            source_metrics=_source_metrics(allocation),
        )
        for allocation in sorted(
            version.allocations,
            key=lambda item: _float(item.target_weight_pct),
            reverse=True,
        )
    ]
    version_payload = ModelPortfolioPublishedVersionDetailResponse(
        **ModelPortfolioVersionResponse.model_validate(version).model_dump(),
        allocations=allocations,
    )
    backtests = sorted(
        version.backtests,
        key=lambda item: (
            item.period_days,
            _float(item.initial_equity_usd),
            item.created_at,
            item.id,
        ),
        reverse=True,
    )
    return ModelPortfolioDetailResponse(
        **ModelPortfolioResponse.model_validate(portfolio).model_dump(),
        current_version=version_payload,
        backtests=[
            PortfolioBacktestResponse.model_validate(backtest) for backtest in backtests
        ],
    )


@router.get("/{slug}/backtests", response_model=list[PortfolioBacktestResponse])
async def get_portfolio_backtests(
    slug: str,
    current_user: CurrentUser,
    db: DBSession,
) -> list[PortfolioBacktestResponse]:
    _ = current_user
    _, version = await _published_detail(db, slug)
    backtests = sorted(
        version.backtests,
        key=lambda item: (
            item.period_days,
            _float(item.initial_equity_usd),
            item.created_at,
            item.id,
        ),
        reverse=True,
    )
    return [
        PortfolioBacktestResponse.model_validate(backtest) for backtest in backtests
    ]


@router.get("/{slug}/explanations", response_model=PortfolioExplanationResponse)
async def get_portfolio_explanations(
    slug: str,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioExplanationResponse:
    _ = current_user
    try:
        return await build_portfolio_explanations(db, slug)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{slug}/weekly-report",
    response_model=PortfolioWeeklyReportResponse | None,
)
async def get_portfolio_weekly_report(
    slug: str,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioWeeklyReportResponse | None:
    _ = current_user
    try:
        return await get_latest_weekly_report(db, slug)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{slug}/weekly-report", response_model=PortfolioWeeklyReportResponse)
async def create_portfolio_weekly_report(
    slug: str,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioWeeklyReportResponse:
    _ = current_user
    try:
        return await generate_weekly_report(db, slug)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.schemas.subscription import (
    DemoClosedPositionItem,
    DemoPortfolioResponse,
    DemoResetResponse,
    DemoTradeItem,
)
from app.services.demo_service import (
    get_demo_closed_position_cycles,
    get_demo_portfolio,
    get_demo_subscription_trades,
    reset_demo_stats,
)

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/portfolio", response_model=DemoPortfolioResponse)
async def demo_portfolio(
    current_user: CurrentUser,
    db: DBSession,
) -> DemoPortfolioResponse:
    """Aggregate simulated P&L stats across all demo subscriptions."""
    return await get_demo_portfolio(db, current_user.id)


@router.post("/reset", response_model=DemoResetResponse)
async def demo_reset(
    current_user: CurrentUser,
    db: DBSession,
) -> DemoResetResponse:
    """Reset simulated trade history while keeping demo subscriptions active."""
    return await reset_demo_stats(db, current_user.id)


@router.get(
    "/subscription/{subscription_id}/trades",
    response_model=list[DemoTradeItem],
)
async def demo_subscription_trades(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DemoTradeItem]:
    """Raw fill history for a specific demo subscription."""
    return await get_demo_subscription_trades(
        db, current_user.id, subscription_id, limit
    )


@router.get(
    "/subscription/{subscription_id}/closed-positions",
    response_model=list[DemoClosedPositionItem],
)
async def demo_closed_positions(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DemoClosedPositionItem]:
    """Closed position cycles (open→close pairs) for a demo subscription."""
    return await get_demo_closed_position_cycles(
        db, current_user.id, subscription_id, limit
    )

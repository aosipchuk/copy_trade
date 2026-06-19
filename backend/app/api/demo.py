from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.schemas.subscription import DemoPortfolioResponse, DemoTradeItem
from app.services.demo_service import get_demo_portfolio, get_demo_subscription_trades

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/portfolio", response_model=DemoPortfolioResponse)
async def demo_portfolio(
    current_user: CurrentUser,
    db: DBSession,
) -> DemoPortfolioResponse:
    """Aggregate simulated P&L stats across all demo subscriptions."""
    return await get_demo_portfolio(db, current_user.id)


@router.get("/subscription/{subscription_id}/trades", response_model=list[DemoTradeItem])
async def demo_subscription_trades(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DemoTradeItem]:
    """Trade history for a specific demo subscription."""
    return await get_demo_subscription_trades(db, current_user.id, subscription_id, limit)

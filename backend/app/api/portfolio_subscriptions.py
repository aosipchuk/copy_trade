from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.portfolio import (
    PortfolioRebalanceApplyResponse,
    PortfolioRebalanceEventResponse,
    PortfolioRebalancePreviewResponse,
    UserPortfolioActivationResponse,
    UserPortfolioSubscriptionCreate,
    UserPortfolioSubscriptionDetailResponse,
    UserPortfolioSubscriptionUpdate,
)
from app.services.portfolio.activation import (
    activate_user_portfolio_subscription,
    cancel_user_portfolio_subscription,
    get_user_portfolio_subscription,
    list_user_portfolio_subscriptions,
)
from app.services.portfolio.billing import BillingPaymentRequiredError
from app.services.portfolio.rebalance import (
    apply_user_portfolio_rebalance,
    list_user_portfolio_rebalance_events,
    preview_user_portfolio_rebalance,
    update_user_portfolio_subscription_settings,
)

router = APIRouter(prefix="/portfolio-subscriptions", tags=["portfolio-subscriptions"])


@router.post("", response_model=UserPortfolioActivationResponse)
async def create(
    body: UserPortfolioSubscriptionCreate,
    current_user: CurrentUser,
    db: DBSession,
    response: Response,
) -> UserPortfolioActivationResponse:
    try:
        result = await activate_user_portfolio_subscription(db, current_user.id, body)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except BillingPaymentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return result.to_response()


@router.get("", response_model=list[UserPortfolioSubscriptionDetailResponse])
async def list_all(
    current_user: CurrentUser,
    db: DBSession,
    is_demo: bool | None = None,
    portfolio_id: int | None = None,
    active_only: bool = True,
) -> list[UserPortfolioSubscriptionDetailResponse]:
    return await list_user_portfolio_subscriptions(
        db,
        current_user.id,
        is_demo=is_demo,
        portfolio_id=portfolio_id,
        active_only=active_only,
    )


@router.get(
    "/{portfolio_subscription_id}",
    response_model=UserPortfolioSubscriptionDetailResponse,
)
async def get(
    portfolio_subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> UserPortfolioSubscriptionDetailResponse:
    try:
        return await get_user_portfolio_subscription(
            db, current_user.id, portfolio_subscription_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.patch(
    "/{portfolio_subscription_id}",
    response_model=UserPortfolioSubscriptionDetailResponse,
)
async def update(
    portfolio_subscription_id: int,
    body: UserPortfolioSubscriptionUpdate,
    current_user: CurrentUser,
    db: DBSession,
) -> UserPortfolioSubscriptionDetailResponse:
    try:
        return await update_user_portfolio_subscription_settings(
            db, current_user.id, portfolio_subscription_id, body
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post(
    "/{portfolio_subscription_id}/preview-rebalance",
    response_model=PortfolioRebalancePreviewResponse,
)
async def preview_rebalance(
    portfolio_subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioRebalancePreviewResponse:
    try:
        return await preview_user_portfolio_rebalance(
            db, current_user.id, portfolio_subscription_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post(
    "/{portfolio_subscription_id}/apply-rebalance",
    response_model=PortfolioRebalanceApplyResponse,
)
async def apply_rebalance(
    portfolio_subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioRebalanceApplyResponse:
    try:
        return await apply_user_portfolio_rebalance(
            db, current_user.id, portfolio_subscription_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get(
    "/{portfolio_subscription_id}/rebalance-history",
    response_model=list[PortfolioRebalanceEventResponse],
)
async def rebalance_history(
    portfolio_subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    limit: int = 20,
) -> list[PortfolioRebalanceEventResponse]:
    try:
        return await list_user_portfolio_rebalance_events(
            db,
            current_user.id,
            portfolio_subscription_id,
            limit=max(1, min(limit, 100)),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.delete(
    "/{portfolio_subscription_id}",
    response_model=UserPortfolioSubscriptionDetailResponse,
)
async def cancel(
    portfolio_subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> UserPortfolioSubscriptionDetailResponse:
    try:
        return await cancel_user_portfolio_subscription(
            db, current_user.id, portfolio_subscription_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

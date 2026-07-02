from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from app.services.subscription_service import (
    create_subscription,
    delete_subscription,
    list_subscriptions,
    update_subscription,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post(
    "", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED
)
async def create(
    body: SubscriptionCreate,
    current_user: CurrentUser,
    db: DBSession,
) -> SubscriptionResponse:
    try:
        return await create_subscription(
            db, current_user.id, body, current_user.hl_address
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("", response_model=list[SubscriptionResponse])
async def list_all(
    current_user: CurrentUser,
    db: DBSession,
    is_demo: bool = False,
) -> list[SubscriptionResponse]:
    return await list_subscriptions(db, current_user.id, is_demo=is_demo)


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
async def update(
    subscription_id: int,
    body: SubscriptionUpdate,
    current_user: CurrentUser,
    db: DBSession,
) -> SubscriptionResponse:
    try:
        return await update_subscription(db, current_user.id, subscription_id, body)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    close_positions: bool = True,
) -> None:
    try:
        await delete_subscription(db, current_user.id, subscription_id, close_positions)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

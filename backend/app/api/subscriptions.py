from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.copy_execution import CopyPreflightResponse
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionResumeResponse,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from app.services.copy_engine.preflight import run_preflight
from app.services.copy_engine.resume import resume_subscription
from app.services.subscription_service import (
    create_subscription,
    delete_subscription,
    get_subscription,
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
    include_inactive: bool = False,
) -> list[SubscriptionResponse]:
    return await list_subscriptions(
        db,
        current_user.id,
        is_demo=is_demo,
        include_inactive=include_inactive,
    )


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_one(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> SubscriptionResponse:
    try:
        return await get_subscription(db, current_user.id, subscription_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post(
    "/{subscription_id}/preflight",
    response_model=CopyPreflightResponse,
)
async def preflight_subscription(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> CopyPreflightResponse:
    try:
        await get_subscription(db, current_user.id, subscription_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return await run_preflight(db, current_user, persist=True)


@router.post(
    "/{subscription_id}/resume",
    response_model=SubscriptionResumeResponse,
)
async def resume(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> SubscriptionResumeResponse:
    try:
        result = await resume_subscription(
            db,
            user=current_user,
            subscription_id=subscription_id,
        )
        response = await get_subscription(db, current_user.id, subscription_id)
        response.baseline_market_count = result.baseline_market_count
        return SubscriptionResumeResponse(
            subscription=response,
            baseline_market_count=result.baseline_market_count,
            warning=result.warning,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


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

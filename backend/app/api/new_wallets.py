from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.models.new_wallet import (
    NewWalletCandidate,
    NewWalletFundingLink,
    UserNewWalletItem,
    UserNewWalletSubscription,
)
from app.models.trade import UserTrade
from app.schemas.new_wallet import (
    AdminNewWalletRescanRequest,
    NewWalletCandidateListResponse,
    NewWalletCandidateResponse,
    NewWalletCandidateStatus,
    NewWalletFundingLinkResponse,
    NewWalletSettingsSnapshot,
    NewWalletSubscriptionCreate,
    NewWalletSummaryResponse,
    UserNewWalletItemResponse,
    UserNewWalletSubscriptionResponse,
)
from app.services.hyperliquid.address import normalize_hl_address
from app.services.hyperliquid.funding_events import get_funding_event_provider
from app.services.new_wallets.activation import (
    cancel_user_new_wallet_subscription,
    create_or_reactivate_new_wallet_subscription,
    get_user_new_wallet_subscription,
    list_user_new_wallet_subscriptions,
)
from app.services.new_wallets.discovery import candidate_status_counts, qualify_address

router = APIRouter(prefix="/new-wallets", tags=["new-wallets"])
subscription_router = APIRouter(
    prefix="/new-wallet-subscriptions",
    tags=["new-wallet-subscriptions"],
)
admin_router = APIRouter(prefix="/admin/new-wallets", tags=["admin-new-wallets"])


@router.get("/candidates", response_model=NewWalletCandidateListResponse)
async def list_candidates(
    current_user: CurrentUser,
    db: DBSession,
    status_filter: NewWalletCandidateStatus | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> NewWalletCandidateListResponse:
    query = (
        select(NewWalletCandidate)
        .order_by(NewWalletCandidate.id.desc())
        .limit(limit + 1)
    )
    if status_filter is not None:
        query = query.where(NewWalletCandidate.status == status_filter)
    if cursor:
        try:
            query = query.where(NewWalletCandidate.id < int(cursor))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor",
            ) from exc

    result = await db.execute(query)
    rows = list(result.scalars().all())
    has_next = len(rows) > limit
    rows = rows[:limit]
    item_map = await _user_item_map(db, current_user.id, [row.id for row in rows])
    items = [
        await _candidate_response(db, candidate, item_map=item_map)
        for candidate in rows
    ]
    return NewWalletCandidateListResponse(
        items=items,
        next_cursor=str(rows[-1].id) if has_next and rows else None,
    )


@router.get("/summary", response_model=NewWalletSummaryResponse)
async def summary(
    current_user: CurrentUser,
    db: DBSession,
) -> NewWalletSummaryResponse:
    active_result = await db.execute(
        select(UserNewWalletSubscription)
        .where(
            UserNewWalletSubscription.user_id == current_user.id,
            UserNewWalletSubscription.status == "active",
        )
        .order_by(UserNewWalletSubscription.created_at.desc())
        .limit(1)
    )
    active = active_result.scalar_one_or_none()
    return NewWalletSummaryResponse(
        counts_by_status=await candidate_status_counts(db),
        active_subscription=(
            await _subscription_response(db, active) if active is not None else None
        ),
        settings=_settings_snapshot(),
    )


@subscription_router.post(
    "",
    response_model=UserNewWalletSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def activate_subscription(
    body: NewWalletSubscriptionCreate,
    current_user: CurrentUser,
    db: DBSession,
) -> UserNewWalletSubscriptionResponse:
    try:
        parent = await create_or_reactivate_new_wallet_subscription(
            db,
            user=current_user,
            data=body,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return await _subscription_response(db, parent)


@subscription_router.get("", response_model=list[UserNewWalletSubscriptionResponse])
async def list_subscriptions(
    current_user: CurrentUser,
    db: DBSession,
) -> list[UserNewWalletSubscriptionResponse]:
    parents = await list_user_new_wallet_subscriptions(db, user_id=current_user.id)
    return [await _subscription_response(db, parent) for parent in parents]


@subscription_router.get(
    "/{subscription_id}",
    response_model=UserNewWalletSubscriptionResponse,
)
async def get_subscription(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
) -> UserNewWalletSubscriptionResponse:
    try:
        parent = await get_user_new_wallet_subscription(
            db,
            user_id=current_user.id,
            subscription_id=subscription_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return await _subscription_response(db, parent)


@subscription_router.delete(
    "/{subscription_id}",
    response_model=UserNewWalletSubscriptionResponse,
)
async def cancel_subscription(
    subscription_id: int,
    current_user: CurrentUser,
    db: DBSession,
    close_positions: bool = True,
) -> UserNewWalletSubscriptionResponse:
    try:
        parent = await cancel_user_new_wallet_subscription(
            db,
            user_id=current_user.id,
            subscription_id=subscription_id,
            close_positions=close_positions,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return await _subscription_response(db, parent)


@admin_router.post("/rescan", response_model=NewWalletCandidateResponse)
async def admin_rescan(
    body: AdminNewWalletRescanRequest,
    _current_user: AdminUser,
    db: DBSession,
) -> NewWalletCandidateResponse:
    try:
        address = normalize_hl_address(body.hl_address)
        candidate = await qualify_address(
            db,
            address,
            provider=get_funding_event_provider(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return await _candidate_response(db, candidate)


async def _candidate_response(
    db: AsyncSession,
    candidate: NewWalletCandidate,
    *,
    item_map: dict[int, UserNewWalletItem] | None = None,
) -> NewWalletCandidateResponse:
    links_result = await db.execute(
        select(NewWalletFundingLink)
        .where(NewWalletFundingLink.candidate_id == candidate.id)
        .order_by(NewWalletFundingLink.depth.asc())
    )
    links = [
        NewWalletFundingLinkResponse(
            id=link.id,
            depth=link.depth,
            wallet_address=link.wallet_address,
            funded_by_address=link.funded_by_address,
            amount_usdc=float(link.amount_usdc)
            if link.amount_usdc is not None
            else None,
            event_time=link.event_time,
            tx_hash=link.tx_hash,
            balance_usd=float(link.balance_usd)
            if link.balance_usd is not None
            else None,
            balance_source=link.balance_source,
        )
        for link in links_result.scalars().all()
    ]
    item = item_map.get(candidate.id) if item_map else None
    return NewWalletCandidateResponse(
        id=candidate.id,
        trader_id=candidate.trader_id,
        hl_address=candidate.hl_address,
        status=candidate.status,  # type: ignore[arg-type]
        detected_at=candidate.detected_at,
        funded_at=candidate.funded_at,
        qualified_at=candidate.qualified_at,
        last_checked_at=candidate.last_checked_at,
        chain_depth=candidate.chain_depth,
        chain_total_balance_usd=float(candidate.chain_total_balance_usd)
        if candidate.chain_total_balance_usd is not None
        else None,
        threshold_usd_snapshot=float(candidate.threshold_usd_snapshot)
        if candidate.threshold_usd_snapshot is not None
        else None,
        reject_reason=candidate.reject_reason,
        first_seen_tx_hash=candidate.first_seen_tx_hash,
        links=links,
        user_item_status=item.status if item else None,  # type: ignore[arg-type]
        user_child_subscription_id=item.subscription_id if item else None,
        user_child_expires_at=item.expires_at if item else None,
    )


async def _subscription_response(
    db: AsyncSession,
    parent: UserNewWalletSubscription,
) -> UserNewWalletSubscriptionResponse:
    items_result = await db.execute(
        select(UserNewWalletItem)
        .where(UserNewWalletItem.user_new_wallet_subscription_id == parent.id)
        .order_by(UserNewWalletItem.created_at.desc())
    )
    items: list[UserNewWalletItemResponse] = []
    for item in items_result.scalars().all():
        candidate = await db.get(NewWalletCandidate, item.candidate_id)
        realized_pnl, trade_count = await _child_trade_stats(
            db,
            subscription_id=item.subscription_id,
        )
        items.append(
            UserNewWalletItemResponse(
                id=item.id,
                candidate_id=item.candidate_id,
                subscription_id=item.subscription_id,
                trader_id=item.trader_id,
                target_allocation_usd=float(item.target_allocation_usd),
                status=item.status,  # type: ignore[arg-type]
                created_at=item.created_at,
                expires_at=item.expires_at,
                ended_at=item.ended_at,
                error_msg=item.error_msg,
                realized_pnl=realized_pnl,
                unrealized_pnl=0.0,
                trade_count=trade_count,
                candidate=(
                    await _candidate_response(db, candidate)
                    if candidate is not None
                    else None
                ),
            )
        )

    return UserNewWalletSubscriptionResponse(
        id=parent.id,
        user_id=parent.user_id,
        status=parent.status,  # type: ignore[arg-type]
        is_demo=parent.is_demo,
        total_allocation_usd=float(parent.total_allocation_usd),
        max_active_wallets=parent.max_active_wallets,
        max_per_wallet_usd=float(parent.max_per_wallet_usd),
        copy_ratio_pct=float(parent.copy_ratio_pct),
        stop_loss_pct=float(parent.stop_loss_pct),
        max_leverage=float(parent.max_leverage),
        sizing_mode=parent.sizing_mode,
        allowed_coins=list(parent.allowed_coins)
        if parent.allowed_coins is not None
        else None,
        close_positions_on_expire=parent.close_positions_on_expire,
        created_at=parent.created_at,
        updated_at=parent.updated_at,
        canceled_at=parent.canceled_at,
        items=items,
    )


async def _child_trade_stats(
    db: AsyncSession,
    *,
    subscription_id: int,
) -> tuple[float, int]:
    result = await db.execute(
        select(
            func.coalesce(func.sum(UserTrade.realized_pnl), 0),
            func.count(UserTrade.id),
        ).where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.status == "filled",
        )
    )
    realized_pnl, trade_count = result.one()
    return float(realized_pnl or 0), int(trade_count)


async def _user_item_map(
    db: AsyncSession,
    user_id: int,
    candidate_ids: list[int],
) -> dict[int, UserNewWalletItem]:
    if not candidate_ids:
        return {}
    result = await db.execute(
        select(UserNewWalletItem)
        .join(
            UserNewWalletSubscription,
            UserNewWalletSubscription.id
            == UserNewWalletItem.user_new_wallet_subscription_id,
        )
        .where(
            UserNewWalletSubscription.user_id == user_id,
            UserNewWalletItem.candidate_id.in_(candidate_ids),
        )
        .order_by(UserNewWalletItem.created_at.desc())
    )
    items: dict[int, UserNewWalletItem] = {}
    for item in result.scalars().all():
        items.setdefault(item.candidate_id, item)
    return items


def _settings_snapshot() -> NewWalletSettingsSnapshot:
    return NewWalletSettingsSnapshot(
        discovery_enabled=settings.new_wallet_discovery_enabled,
        auto_attach_enabled=settings.new_wallet_auto_attach_enabled,
        funding_provider_configured=bool(settings.new_wallet_funding_events_url),
        chain_balance_threshold_usd=settings.new_wallet_chain_balance_threshold_usd,
        max_chain_depth=settings.new_wallet_max_chain_depth,
        subscription_ttl_days=settings.new_wallet_subscription_ttl_days,
        min_incoming_amount_usd=settings.new_wallet_min_incoming_amount_usd,
        max_active_per_user=settings.new_wallet_max_active_per_user,
        default_max_per_wallet_usd=settings.new_wallet_default_max_per_wallet_usd,
    )

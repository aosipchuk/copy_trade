import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.models.user import User, UserAgent
from app.schemas.wallet import (
    ActivityItem,
    AgentStatusResponse,
    CloseAllResponse,
    PortfolioRiskResponse,
    PortfolioRiskUpdate,
    WalletApproveRequest,
    WalletBalanceResponse,
    WalletBuilderApproveRequest,
    WalletBuilderSetupResponse,
    WalletPositionItem,
    WalletSetupResponse,
)
from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.wallet.agent_manager import encrypt_agent_key, generate_agent_keypair

logger = get_logger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.post("/setup", response_model=WalletSetupResponse)
async def wallet_setup(
    current_user: CurrentUser,
    db: DBSession,
) -> WalletSetupResponse:
    """
    Generate a new agent keypair and return the EIP-712 payload for the user to sign.
    Persists the inactive agent row; activation happens on /wallet/approve.
    """
    # Deactivate any existing active agents
    existing = await db.execute(
        select(UserAgent).where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active == True,  # noqa: E712
        )
    )
    for agent in existing.scalars().all():
        agent.is_active = False

    # Delete stale pending agents (never approved) to avoid MultipleResultsFound
    from sqlalchemy import delete

    await db.execute(
        delete(UserAgent).where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active == False,  # noqa: E712
            UserAgent.approved_at.is_(None),
        )
    )

    keypair = generate_agent_keypair()
    encrypted = encrypt_agent_key(keypair.private_key)
    nonce = int(time.time() * 1000)

    agent = UserAgent(
        user_id=current_user.id,
        agent_address=keypair.address,
        agent_key_enc=encrypted,
        setup_nonce=nonce,
        is_active=False,  # activated only after approveAgent
    )
    db.add(agent)

    exchange = HyperliquidExchangeClient()
    payload = exchange.build_approve_agent_payload(keypair.address, nonce)

    return WalletSetupResponse(
        agent_address=keypair.address,
        nonce=nonce,
        eip712_payload=payload,
    )


@router.post("/approve", status_code=status.HTTP_204_NO_CONTENT)
async def wallet_approve(
    current_user: CurrentUser,
    db: DBSession,
    body: WalletApproveRequest,
) -> None:
    """
    Receive the user's EIP-712 signature for approveAgent, relay to Hyperliquid,
    and mark the agent as active.
    """
    agent_res = await db.execute(
        select(UserAgent)
        .where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active == False,  # noqa: E712
            UserAgent.approved_at.is_(None),
        )
        .order_by(UserAgent.created_at.desc())
        .limit(1)
    )
    agent = agent_res.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending agent found. Call /wallet/setup first.",
        )

    if agent.setup_nonce is not None and body.nonce != agent.setup_nonce:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nonce mismatch. Please restart wallet setup.",
        )

    # Update user HL address if provided
    if body.user_address:
        user_res = await db.execute(select(User).where(User.id == current_user.id))
        user = user_res.scalar_one()
        user.hl_address = body.user_address

    if not settings.hl_skip_approve:
        exchange = HyperliquidExchangeClient()
        hl_error = await exchange.submit_approve_agent(
            agent_address=agent.agent_address,
            nonce=body.nonce,
            signature=body.signature,
        )
        if hl_error is not None:
            logger.warning(
                "approve_agent_rejected_by_hl",
                hl_error=hl_error,
                user_id=current_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Wallet approval failed: {hl_error}",
            )

    from datetime import UTC, datetime

    agent.approved_at = datetime.now(tz=UTC).replace(tzinfo=None)
    agent.is_active = True


@router.get("/balance", response_model=WalletBalanceResponse)
async def wallet_balance(current_user: CurrentUser) -> WalletBalanceResponse:
    if not current_user.hl_address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No HL address configured. Complete wallet setup first.",
        )
    hl = HyperliquidInfoClient()
    summary = await hl.get_account_summary(current_user.hl_address)
    return WalletBalanceResponse(
        account_value=float(summary.account_value),
        total_margin_used=float(summary.total_margin_used),
        available=float(summary.account_value - summary.total_margin_used),
    )


@router.get("/positions", response_model=list[WalletPositionItem])
async def wallet_positions(
    current_user: CurrentUser,
    db: DBSession,
) -> list[WalletPositionItem]:
    if not current_user.hl_address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No HL address configured.",
        )
    hl = HyperliquidInfoClient()
    positions = await hl.get_positions(current_user.hl_address)

    # Build coin → subscription_id mapping from most recent trades
    trade_res = await db.execute(
        select(UserTrade.coin, UserTrade.subscription_id)
        .join(Subscription, UserTrade.subscription_id == Subscription.id)
        .where(Subscription.user_id == current_user.id)
        .order_by(UserTrade.executed_at.desc())
    )
    coin_to_sub: dict[str, int] = {}
    for coin, sub_id in trade_res.all():
        if coin not in coin_to_sub:
            coin_to_sub[coin] = sub_id

    return [
        WalletPositionItem(
            coin=p.coin,
            side=p.side,
            size=float(p.abs_size),
            entry_px=float(p.entry_px) if p.entry_px else None,
            unrealized_pnl=float(p.unrealized_pnl),
            leverage=p.leverage.value,
            subscription_id=coin_to_sub.get(p.coin),
        )
        for p in positions
    ]


@router.post("/close-all", response_model=CloseAllResponse, status_code=202)
@limiter.limit("3/minute")
async def close_all_positions(
    request: Request,
    current_user: CurrentUser,
    db: DBSession,
) -> CloseAllResponse:
    """
    Emergency stop: deactivate all subscriptions and close all open HL positions.
    Returns immediately with counts; actual closes execute in a background Celery task.
    """
    if not current_user.hl_address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No HL address configured.",
        )

    # Count open positions
    hl = HyperliquidInfoClient()
    positions = await hl.get_positions(current_user.hl_address)
    position_count = len(positions)

    # Deactivate all active subscriptions
    sub_res = await db.execute(
        select(Subscription).where(
            Subscription.user_id == current_user.id,
            Subscription.is_active == True,  # noqa: E712
        )
    )
    subs = sub_res.scalars().all()
    sub_count = len(subs)
    for sub in subs:
        sub.is_active = False

    # Schedule close in background (fire-and-forget)
    from app.tasks.execution_tasks import close_all_positions_for_user_async

    asyncio.create_task(close_all_positions_for_user_async(current_user.id))

    return CloseAllResponse(closed=position_count, subscriptions_paused=sub_count)


@router.get("/activity", response_model=list[ActivityItem])
async def wallet_activity(
    current_user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ActivityItem]:
    """Return the last N agent actions for the current user."""
    result = await db.execute(
        select(
            UserTrade.coin,
            UserTrade.side,
            UserTrade.size,
            UserTrade.executed_at,
            UserTrade.status,
            Signal.signal_type,
            Trader.display_name,
            Trader.hl_address,
        )
        .join(Signal, UserTrade.signal_id == Signal.id)
        .join(Subscription, UserTrade.subscription_id == Subscription.id)
        .join(Trader, Subscription.trader_id == Trader.id)
        .where(Subscription.user_id == current_user.id)
        .order_by(UserTrade.executed_at.desc())
        .limit(limit)
    )
    rows = result.all()

    items: list[ActivityItem] = []
    for (
        coin,
        side,
        size,
        executed_at,
        trade_status,
        signal_type,
        trader_name,
        trader_addr,
    ) in rows:
        if trade_status == "failed":
            action = "trade_failed"
        elif trade_status == "cancelled":
            action = "trade_cancelled"
        elif signal_type == "CLOSE":
            action = "position_closed"
        elif signal_type == "UPDATE":
            action = "position_updated"
        else:
            action = "trade_executed"

        short_addr = f"{trader_addr[:6]}…{trader_addr[-4:]}" if trader_addr else None
        items.append(
            ActivityItem(
                action=action,
                coin=coin,
                side=side,
                size=float(size) if size is not None else None,
                pnl=None,
                ts=executed_at,
                subscription_trader=trader_name or short_addr,
            )
        )

    return items


@router.delete("/agent", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(current_user: CurrentUser, db: DBSession) -> None:
    """Deactivate the current agent. The user must re-run setup to resume copy-trading."""  # noqa: E501
    result = await db.execute(
        select(UserAgent).where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active == True,  # noqa: E712
        )
    )
    for agent in result.scalars().all():
        agent.is_active = False


@router.get("/portfolio-risk", response_model=PortfolioRiskResponse)
async def get_portfolio_risk(current_user: CurrentUser) -> PortfolioRiskResponse:
    """Return the user's current portfolio-level stop-loss setting."""
    psl = current_user.portfolio_stop_loss_pct
    return PortfolioRiskResponse(
        portfolio_stop_loss_pct=float(psl) if psl is not None else None
    )


@router.patch("/portfolio-risk", response_model=PortfolioRiskResponse)
async def update_portfolio_risk(
    current_user: CurrentUser,
    db: DBSession,
    body: PortfolioRiskUpdate,
) -> PortfolioRiskResponse:
    """Update the portfolio-level stop-loss percentage. Pass null to disable."""
    user_res = await db.execute(select(User).where(User.id == current_user.id))
    user = user_res.scalar_one()
    user.portfolio_stop_loss_pct = body.portfolio_stop_loss_pct
    psl = user.portfolio_stop_loss_pct
    return PortfolioRiskResponse(
        portfolio_stop_loss_pct=float(psl) if psl is not None else None
    )


@router.get("/builder-setup", response_model=WalletBuilderSetupResponse)
async def builder_fee_setup(current_user: CurrentUser) -> WalletBuilderSetupResponse:
    """Return EIP-712 payload for ApproveBuilderFee user signature."""
    if not settings.builder_address:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Builder fee not configured.",
        )
    nonce = int(time.time() * 1000)
    exchange = HyperliquidExchangeClient()
    payload = exchange.build_approve_builder_fee_payload(nonce)
    return WalletBuilderSetupResponse(nonce=nonce, eip712_payload=payload)


@router.post("/builder-approve", status_code=status.HTTP_204_NO_CONTENT)
async def builder_fee_approve(
    current_user: CurrentUser,
    db: DBSession,
    body: WalletBuilderApproveRequest,
) -> None:
    """Submit user's EIP-712 signature for ApproveBuilderFee to Hyperliquid."""
    if not settings.hl_skip_approve:
        exchange = HyperliquidExchangeClient()
        hl_error = await exchange.submit_approve_builder_fee(
            nonce=body.nonce,
            signature=body.signature,
        )
        if hl_error is not None:
            logger.warning(
                "approve_builder_fee_rejected_by_hl",
                hl_error=hl_error,
                user_id=current_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Builder fee approval failed: {hl_error}",
            )

    from datetime import UTC, datetime

    user_res = await db.execute(select(User).where(User.id == current_user.id))
    user = user_res.scalar_one()
    user.builder_fee_approved_at = datetime.now(tz=UTC).replace(tzinfo=None)


@router.get("/status", response_model=AgentStatusResponse)
async def agent_status(current_user: CurrentUser, db: DBSession) -> AgentStatusResponse:
    result = await db.execute(
        select(UserAgent).where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active == True,  # noqa: E712
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        return AgentStatusResponse(
            agent_address=None,
            is_active=False,
            approved_at=None,
            builder_fee_approved=False,
        )
    return AgentStatusResponse(
        agent_address=agent.agent_address,
        is_active=agent.is_active,
        approved_at=agent.approved_at.isoformat() if agent.approved_at else None,
        builder_fee_approved=current_user.builder_fee_approved_at is not None,
    )

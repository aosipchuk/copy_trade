from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import CopyAccountExecutionState
from app.models.new_wallet import UserNewWalletItem, UserNewWalletSubscription
from app.models.portfolio import UserPortfolioItem, UserPortfolioSubscription
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.models.user import User
from app.services.copy_engine.execution_state import utcnow_naive
from app.services.copy_engine.locking import lock_user_account
from app.services.copy_engine.preflight import run_preflight
from app.services.copy_engine.resume import initialize_subscription_baseline


@dataclass(frozen=True)
class ManagedResumeResult:
    child_count: int
    baseline_market_count: int
    warning: str


async def _resume_children(
    db: AsyncSession,
    *,
    user: User,
    children: list[Subscription],
) -> ManagedResumeResult:
    preflight = await run_preflight(db, user, persist=True)
    if not preflight.ok:
        failures = [check.code for check in preflight.checks if not check.ok]
        raise ValueError("Preflight failed: " + ", ".join(failures))
    baseline_count = 0
    for child in children:
        trader = await db.get(Trader, child.trader_id)
        if trader is None:
            raise ValueError(f"Trader {child.trader_id} no longer exists")
        baseline_count += await initialize_subscription_baseline(db, child, trader)
        child.engine_version = 2
        child.execution_status = "active"
        child.pause_reason = None
        child.is_active = True
        child.resumed_at = utcnow_naive()
    state = await db.get(CopyAccountExecutionState, user.id)
    if state is None:
        raise ValueError("Copy account state is missing")
    state.status = "active"
    state.reason = None
    state.details = None
    state.version += 1
    return ManagedResumeResult(
        child_count=len(children),
        baseline_market_count=baseline_count,
        warning=(
            "All current leader positions were initialized as baseline-only and "
            "will not be copied until a close followed by a new open."
        ),
    )


async def resume_portfolio_parent(
    db: AsyncSession,
    *,
    user: User,
    parent_id: int,
) -> ManagedResumeResult:
    await lock_user_account(db, user.id)
    parent = await db.get(UserPortfolioSubscription, parent_id)
    if parent is None or parent.user_id != user.id:
        raise LookupError("Portfolio subscription not found")
    if parent.is_demo:
        raise ValueError("Demo portfolio execution is already immediate")
    result = await db.execute(
        select(Subscription)
        .join(UserPortfolioItem, UserPortfolioItem.subscription_id == Subscription.id)
        .where(
            UserPortfolioItem.user_portfolio_subscription_id == parent.id,
            UserPortfolioItem.status.in_(("paused", "active")),
        )
        .order_by(Subscription.id)
    )
    children = list(result.scalars().all())
    resumed = await _resume_children(db, user=user, children=children)
    parent.engine_version = 2
    parent.execution_status = "active"
    parent.pause_reason = None
    parent.resumed_at = utcnow_naive()
    item_result = await db.execute(
        select(UserPortfolioItem).where(
            UserPortfolioItem.user_portfolio_subscription_id == parent.id,
            UserPortfolioItem.status == "paused",
        )
    )
    for item in item_result.scalars().all():
        item.status = "active"
    return resumed


async def resume_new_wallet_parent(
    db: AsyncSession,
    *,
    user: User,
    parent_id: int,
) -> ManagedResumeResult:
    await lock_user_account(db, user.id)
    parent = await db.get(UserNewWalletSubscription, parent_id)
    if parent is None or parent.user_id != user.id:
        raise LookupError("New-wallet subscription not found")
    if parent.is_demo:
        raise ValueError("Demo new-wallet execution is already immediate")
    result = await db.execute(
        select(Subscription)
        .join(UserNewWalletItem, UserNewWalletItem.subscription_id == Subscription.id)
        .where(
            UserNewWalletItem.user_new_wallet_subscription_id == parent.id,
            UserNewWalletItem.status.in_(("paused", "active")),
        )
        .order_by(Subscription.id)
    )
    children = list(result.scalars().all())
    resumed = await _resume_children(db, user=user, children=children)
    parent.engine_version = 2
    parent.execution_status = "active"
    parent.pause_reason = None
    parent.resumed_at = utcnow_naive()
    item_result = await db.execute(
        select(UserNewWalletItem).where(
            UserNewWalletItem.user_new_wallet_subscription_id == parent.id,
            UserNewWalletItem.status == "paused",
        )
    )
    for item in item_result.scalars().all():
        item.status = "active"
    return resumed

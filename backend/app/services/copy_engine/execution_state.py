from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import CopyAccountExecutionState
from app.models.new_wallet import UserNewWalletSubscription
from app.models.portfolio import UserPortfolioSubscription
from app.models.subscription import Subscription
from app.services.copy_engine.locking import lock_user_account


def utcnow_naive() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def get_or_create_account_state(
    db: AsyncSession,
    user_id: int,
) -> CopyAccountExecutionState:
    state = await db.get(CopyAccountExecutionState, user_id)
    if state is None:
        state = CopyAccountExecutionState(
            user_id=user_id,
            status="paused",
            reason="copy_account_not_configured",
        )
        db.add(state)
        await db.flush()
    return state


async def _set_children_status(
    db: AsyncSession,
    user_id: int,
    status: str,
    reason: str,
    details: dict[str, Any] | None,
) -> None:
    values: dict[str, Any] = {
        "execution_status": status,
        "pause_reason": reason,
        "execution_status_details": details,
    }
    if status == "blocked":
        values["blocked_at"] = utcnow_naive()
    await db.execute(
        update(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.is_demo.is_(False),
            Subscription.engine_version == 2,
        )
        .values(**values)
    )
    await db.execute(
        update(UserPortfolioSubscription)
        .where(
            UserPortfolioSubscription.user_id == user_id,
            UserPortfolioSubscription.is_demo.is_(False),
            UserPortfolioSubscription.engine_version == 2,
        )
        .values(**values)
    )
    await db.execute(
        update(UserNewWalletSubscription)
        .where(
            UserNewWalletSubscription.user_id == user_id,
            UserNewWalletSubscription.is_demo.is_(False),
            UserNewWalletSubscription.engine_version == 2,
        )
        .values(**values)
    )


async def pause_account(
    db: AsyncSession,
    user_id: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> CopyAccountExecutionState:
    await lock_user_account(db, user_id)
    state = await get_or_create_account_state(db, user_id)
    state.status = "paused"
    state.reason = reason
    state.details = details
    state.version += 1
    await _set_children_status(db, user_id, "paused", reason, details)
    return state


async def block_account(
    db: AsyncSession,
    user_id: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> CopyAccountExecutionState:
    await lock_user_account(db, user_id)
    state = await get_or_create_account_state(db, user_id)
    if state.status == "blocked" and state.reason == reason:
        return state
    state.status = "blocked"
    state.reason = reason
    state.details = details
    state.blocked_at = utcnow_naive()
    state.version += 1
    await _set_children_status(db, user_id, "blocked", reason, details)
    return state


async def clear_block_after_flat_preflight(
    db: AsyncSession,
    user_id: int,
) -> CopyAccountExecutionState:
    await lock_user_account(db, user_id)
    state = await get_or_create_account_state(db, user_id)
    state.status = "paused"
    state.reason = "manual_resume_required"
    state.details = None
    state.cleared_at = utcnow_naive()
    state.version += 1
    return state


async def has_active_v2_subscription(db: AsyncSession, user_id: int) -> bool:
    result = await db.execute(
        select(Subscription.id).where(
            Subscription.user_id == user_id,
            Subscription.is_demo.is_(False),
            Subscription.engine_version == 2,
            Subscription.execution_status == "active",
            Subscription.is_active.is_(True),
        )
    )
    return result.first() is not None

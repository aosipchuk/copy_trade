import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.portfolio import UserPortfolioItem, UserPortfolioSubscription
from app.models.signal import Signal
from app.models.subscription import Subscription

PORTFOLIO_EXECUTION_STATUSES = ("trialing", "active")


@dataclass(frozen=True)
class ExecutableSubscriptionTarget:
    subscription_id: int
    user_id: int
    is_demo: bool


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def subscription_execution_allowed_clause() -> ColumnElement[bool]:
    """SQLAlchemy filter for subscriptions that may receive new copied signals."""
    portfolio_subscription_is_active = exists().where(
        UserPortfolioSubscription.id == Subscription.source_id,
        UserPortfolioSubscription.user_id == Subscription.user_id,
        UserPortfolioSubscription.is_demo == Subscription.is_demo,
        UserPortfolioSubscription.status.in_(PORTFOLIO_EXECUTION_STATUSES),
    )
    return or_(
        and_(
            Subscription.source_type == "manual",
            Subscription.managed_by_portfolio.is_(False),
        ),
        and_(
            Subscription.source_type == "model_portfolio",
            Subscription.source_id.is_not(None),
            Subscription.managed_by_portfolio.is_(True),
            portfolio_subscription_is_active,
        ),
    )


async def executable_subscription_targets_for_signal(
    db: AsyncSession,
    signal_id: int,
) -> list[ExecutableSubscriptionTarget]:
    signal_result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = signal_result.scalar_one_or_none()
    if signal is None:
        return []

    result = await db.execute(
        select(Subscription.id, Subscription.user_id, Subscription.is_demo).where(
            Subscription.trader_id == signal.trader_id,
            Subscription.is_active.is_(True),
            subscription_execution_allowed_clause(),
        )
    )
    return [
        ExecutableSubscriptionTarget(
            subscription_id=int(subscription_id),
            user_id=int(user_id),
            is_demo=bool(is_demo),
        )
        for subscription_id, user_id, is_demo in result.all()
    ]


async def lock_user_portfolio_subscription_slot(
    db: AsyncSession,
    *,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
    is_demo: bool,
) -> None:
    key = (
        "model-portfolio-subscription:"
        f"{user_id}:{portfolio_id}:{active_version_id}:{int(is_demo)}"
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
        {"key": key},
    )


async def deactivate_portfolio_owned_subscriptions(
    db: AsyncSession,
    portfolio_subscription: UserPortfolioSubscription,
    *,
    close_positions: bool = False,
) -> int:
    """Deactivate only generated subscriptions owned by a portfolio subscription."""
    now = _now()
    items_result = await db.execute(
        select(UserPortfolioItem).where(
            UserPortfolioItem.user_portfolio_subscription_id
            == portfolio_subscription.id,
            UserPortfolioItem.status == "active",
        )
    )
    items = list(items_result.scalars().all())
    for item in items:
        item.status = "removed"
        item.removed_at = now

    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == portfolio_subscription.user_id,
            Subscription.source_type == "model_portfolio",
            Subscription.source_id == portfolio_subscription.id,
            Subscription.managed_by_portfolio.is_(True),
            Subscription.is_active.is_(True),
        )
    )
    subscriptions = list(result.scalars().all())
    for subscription in subscriptions:
        subscription.is_active = False

    if close_positions and not portfolio_subscription.is_demo:
        from app.tasks.execution_tasks import close_subscription_positions_async

        for subscription in subscriptions:
            asyncio.create_task(
                close_subscription_positions_async(
                    portfolio_subscription.user_id,
                    subscription.id,
                )
            )

    await db.flush()
    return len(subscriptions)

import asyncio

from sqlalchemy import select

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioVersion,
    UserPortfolioSubscription,
)
from app.services.portfolio.explanations import generate_weekly_report
from app.services.portfolio.rebalance import apply_user_portfolio_rebalance

logger = get_logger(__name__)

_LOCK_KEY = "lock:portfolio:auto_rebalance"
_REPORT_LOCK_KEY = "lock:portfolio:weekly_reports"
_LOCK_TTL_SECONDS = 240
_REPORT_LOCK_TTL_SECONDS = 900


async def _acquire_lock(key: str, ttl_seconds: int) -> bool:
    try:
        redis = get_redis_client()
        acquired = await asyncio.to_thread(
            redis.set,
            key,
            "1",
            nx=True,
            ex=ttl_seconds,
        )
        return bool(acquired)
    except Exception as exc:
        logger.warning(
            "portfolio_auto_rebalance_lock_unavailable",
            error=str(exc),
        )
        return True


async def _release_lock(key: str) -> None:
    try:
        redis = get_redis_client()
        await asyncio.to_thread(redis.delete, key)
    except Exception as exc:
        logger.warning(
            "portfolio_auto_rebalance_lock_release_failed",
            error=str(exc),
        )


async def _due_auto_rebalance_subscription_ids() -> list[tuple[int, int]]:
    async with get_db_session() as db:
        result = await db.execute(
            select(UserPortfolioSubscription.id, UserPortfolioSubscription.user_id)
            .join(
                ModelPortfolioVersion,
                ModelPortfolioVersion.portfolio_id
                == UserPortfolioSubscription.portfolio_id,
            )
            .where(
                UserPortfolioSubscription.auto_rebalance.is_(True),
                UserPortfolioSubscription.status.in_(
                    ("trialing", "active", "past_due", "paused")
                ),
                ModelPortfolioVersion.status == "published",
                ModelPortfolioVersion.valid_to.is_(None),
                ModelPortfolioVersion.id != UserPortfolioSubscription.active_version_id,
            )
            .order_by(UserPortfolioSubscription.id.asc())
        )
        return [(int(row[0]), int(row[1])) for row in result.all()]


async def apply_due_user_rebalances_async() -> None:
    """Apply current published portfolio versions to eligible auto-rebalance users."""
    if not await _acquire_lock(_LOCK_KEY, _LOCK_TTL_SECONDS):
        logger.info("portfolio_auto_rebalance_skipped_locked")
        return

    processed = 0
    skipped = 0
    failed = 0
    try:
        rows = await _due_auto_rebalance_subscription_ids()
        for portfolio_subscription_id, user_id in rows:
            async with get_db_session() as db:
                try:
                    result = await apply_user_portfolio_rebalance(
                        db,
                        user_id,
                        portfolio_subscription_id,
                        event_type="scheduled",
                    )
                    if result.event.status == "completed":
                        processed += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "portfolio_auto_rebalance_failed",
                        user_id=user_id,
                        portfolio_subscription_id=portfolio_subscription_id,
                        error=str(exc),
                    )
        logger.info(
            "portfolio_auto_rebalance_done",
            processed=processed,
            skipped=skipped,
            failed=failed,
        )
    finally:
        await _release_lock(_LOCK_KEY)


async def _active_published_portfolio_slugs() -> list[str]:
    async with get_db_session() as db:
        result = await db.execute(
            select(ModelPortfolio.slug)
            .join(
                ModelPortfolioVersion,
                ModelPortfolioVersion.portfolio_id == ModelPortfolio.id,
            )
            .where(
                ModelPortfolio.status == "active",
                ModelPortfolioVersion.status == "published",
                ModelPortfolioVersion.valid_to.is_(None),
            )
            .order_by(ModelPortfolio.id.asc())
        )
        return [str(slug) for slug in result.scalars().all()]


async def generate_weekly_portfolio_reports_async() -> None:
    """Persist weekly explanation reports for active published portfolios."""
    if not await _acquire_lock(_REPORT_LOCK_KEY, _REPORT_LOCK_TTL_SECONDS):
        logger.info("portfolio_weekly_reports_skipped_locked")
        return

    generated = 0
    failed = 0
    try:
        slugs = await _active_published_portfolio_slugs()
        for slug in slugs:
            async with get_db_session() as db:
                try:
                    await generate_weekly_report(db, slug)
                    generated += 1
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "portfolio_weekly_report_failed",
                        portfolio_slug=slug,
                        error=str(exc),
                    )
        logger.info(
            "portfolio_weekly_reports_done",
            generated=generated,
            failed=failed,
        )
    finally:
        await _release_lock(_REPORT_LOCK_KEY)

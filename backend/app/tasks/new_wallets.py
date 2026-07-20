from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.new_wallet import UserNewWalletItem, UserNewWalletSubscription
from app.models.subscription import Subscription
from app.services.hyperliquid.funding_events import (
    FundingEventProviderUnavailable,
    get_funding_event_provider,
)
from app.services.new_wallets.activation import attach_qualified_new_wallets
from app.services.new_wallets.discovery import (
    discovery_start_time,
    event_passes_min_amount,
    pending_candidates_for_qualification,
    qualify_candidate,
    upsert_candidate_from_event,
)

logger = get_logger(__name__)

_DISCOVERY_LOCK_KEY = "new_wallets:discovery:lock"
_DISCOVERY_CURSOR_KEY = "new_wallets:discovery:cursor"
_DISCOVERY_LAST_MS_KEY = "new_wallets:discovery:last_ms"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _acquire_lock(key: str, ttl_seconds: int) -> bool:
    redis_cli = get_redis_client()
    return bool(redis_cli.set(key, "1", nx=True, ex=ttl_seconds))


async def discover_new_wallets_async() -> None:
    if not settings.new_wallet_discovery_enabled:
        return
    if not _acquire_lock(
        _DISCOVERY_LOCK_KEY,
        max(60, settings.new_wallet_scan_interval_seconds * 2),
    ):
        return

    redis_cli = get_redis_client()
    cursor: str | None = redis_cli.get(_DISCOVERY_CURSOR_KEY)
    last_ms: str | None = redis_cli.get(_DISCOVERY_LAST_MS_KEY)
    start_time = discovery_start_time()
    if last_ms and not cursor:
        start_time = datetime.fromtimestamp(
            int(last_ms) / 1000,
            tz=UTC,
        ).replace(tzinfo=None)
    provider = get_funding_event_provider()

    try:
        batch = await provider.fetch_events_since(
            start_time=start_time,
            cursor=cursor,
            limit=settings.new_wallet_max_candidates_per_run,
        )
    except FundingEventProviderUnavailable as exc:
        logger.warning("new_wallet_provider_unavailable", error=str(exc))
        return
    except Exception as exc:
        logger.error("new_wallet_event_fetch_failed", error=str(exc))
        return

    scanned = 0
    ingested = 0
    qualified = 0
    rejected = 0
    failed = 0

    async with get_db_session() as db:
        for event in batch.events:
            scanned += 1
            if not event_passes_min_amount(event):
                continue
            candidate = await upsert_candidate_from_event(db, event)
            ingested += 1
            logger.info(
                "new_wallet_event_ingested",
                candidate_id=candidate.id,
                target=event.target_address,
                tx_hash=event.tx_hash,
            )

        candidates = await pending_candidates_for_qualification(
            db,
            limit=settings.new_wallet_max_candidates_per_run,
        )
        for candidate in candidates:
            try:
                await qualify_candidate(db, candidate, provider=provider)
            except Exception as exc:
                failed += 1
                logger.error(
                    "new_wallet_candidate_qualification_failed",
                    candidate_id=candidate.id,
                    address=candidate.hl_address,
                    error=str(exc),
                )
                continue
            if candidate.status in {"qualified", "subscribed"}:
                qualified += 1
            elif candidate.status == "rejected":
                rejected += 1

    if batch.next_cursor:
        redis_cli.set(_DISCOVERY_CURSOR_KEY, batch.next_cursor)
    else:
        redis_cli.delete(_DISCOVERY_CURSOR_KEY)
        if batch.events:
            max_event_ms = max(
                int(event.event_time.replace(tzinfo=UTC).timestamp() * 1000)
                for event in batch.events
            )
            redis_cli.set(_DISCOVERY_LAST_MS_KEY, str(max_event_ms + 1))
    if scanned:
        redis_cli.incrby("new_wallets:metrics:events_scanned", scanned)
    if qualified:
        redis_cli.incrby("new_wallets:metrics:candidates_qualified", qualified)
    if rejected:
        redis_cli.incrby("new_wallets:metrics:candidates_rejected", rejected)
    if failed:
        redis_cli.incrby("new_wallets:metrics:qualification_failures", failed)

    logger.info(
        "new_wallet_discovery_run_complete",
        scanned=scanned,
        ingested=ingested,
        qualified=qualified,
        rejected=rejected,
        failed=failed,
    )


async def attach_qualified_new_wallets_async() -> None:
    if (
        not settings.new_wallet_discovery_enabled
        or not settings.new_wallet_auto_attach_enabled
    ):
        return
    async with get_db_session() as db:
        attached = await attach_qualified_new_wallets(db)
    if attached:
        get_redis_client().incrby("new_wallets:metrics:users_attached", attached)
        logger.info("new_wallet_attach_run_complete", attached=attached)


async def expire_new_wallet_subscriptions_async() -> None:
    now = _utcnow()
    live_to_close: list[tuple[int, int]] = []
    expired_count = 0

    async with get_db_session() as db:
        result = await db.execute(
            select(UserNewWalletItem, Subscription, UserNewWalletSubscription)
            .join(Subscription, Subscription.id == UserNewWalletItem.subscription_id)
            .join(
                UserNewWalletSubscription,
                UserNewWalletSubscription.id
                == UserNewWalletItem.user_new_wallet_subscription_id,
            )
            .where(
                UserNewWalletItem.status == "active",
                UserNewWalletItem.expires_at <= now,
                Subscription.source_type == "new_wallet",
            )
        )
        rows = result.all()
        for item, subscription, parent in rows:
            subscription.is_active = False
            subscription.ended_reason = "new_wallet_ttl_expired"
            item.status = "expired"
            item.ended_at = now
            expired_count += 1

            if subscription.is_demo:
                try:
                    from app.services.demo_service import (
                        close_demo_subscription_positions,
                    )

                    await close_demo_subscription_positions(db, subscription)
                except Exception as exc:
                    item.status = "failed"
                    item.error_msg = str(exc)
                    logger.error(
                        "new_wallet_close_positions_failed",
                        subscription_id=subscription.id,
                        is_demo=True,
                        error=str(exc),
                    )
                    get_redis_client().incrby(
                        "new_wallets:metrics:expiry_failures",
                        1,
                    )
            else:
                live_to_close.append((subscription.user_id, subscription.id))

    for user_id, subscription_id in live_to_close:
        try:
            from app.tasks.execution_tasks import close_subscription_positions_async

            await close_subscription_positions_async(user_id, subscription_id)
        except Exception as exc:
            logger.error(
                "new_wallet_close_positions_failed",
                subscription_id=subscription_id,
                is_demo=False,
                error=str(exc),
            )
            get_redis_client().incrby("new_wallets:metrics:expiry_failures", 1)

    if expired_count:
        logger.info(
            "new_wallet_subscription_expired",
            count=expired_count,
            live_close_count=len(live_to_close),
        )

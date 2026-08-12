from sqlalchemy import select

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyAccountPosition,
    CopyExecutionOrder,
)
from app.services.copy_engine.drift_detector import detect_account_drift
from app.services.copy_engine.fill_ingestion import ingest_account_fills
from app.services.copy_engine.intent_service import ACTIVE_INTENT_STATUSES
from app.services.copy_engine.reconciler import reconcile_market, recover_intent

logger = get_logger(__name__)

_BATCH_SIZE = 100


async def reconcile_copy_engine_async() -> None:
    async with get_db_session() as db:
        account_result = await db.execute(
            select(CopyAccountExecutionState.user_id).where(
                CopyAccountExecutionState.status == "active"
            )
        )
        user_ids = list(account_result.scalars().all())
    for user_id in user_ids:
        try:
            await ingest_account_fills(int(user_id))
            await detect_account_drift(int(user_id))
        except Exception as exc:
            logger.warning(
                "copy_account_monitor_failed",
                user_id=user_id,
                error=str(exc),
            )

    async with get_db_session() as db:
        intent_result = await db.execute(
            select(CopyExecutionOrder.id)
            .where(CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES))
            .order_by(CopyExecutionOrder.created_at)
            .limit(_BATCH_SIZE)
        )
        intent_ids = list(intent_result.scalars().all())
    for intent_id in intent_ids:
        try:
            await recover_intent(intent_id)
        except Exception as exc:
            logger.warning("copy_intent_recovery_failed", intent_id=intent_id, error=str(exc))

    async with get_db_session() as db:
        position_result = await db.execute(
            select(
                CopyAccountPosition.user_id,
                CopyAccountPosition.dex,
                CopyAccountPosition.coin,
            )
            .where(CopyAccountPosition.status.in_(("dirty", "stalled")))
            .order_by(CopyAccountPosition.updated_at)
            .limit(_BATCH_SIZE)
        )
        markets = list(position_result.all())
    for user_id, dex, coin in markets:
        try:
            await reconcile_market(int(user_id), str(dex), str(coin))
        except Exception as exc:
            logger.warning(
                "copy_market_reconcile_failed",
                user_id=user_id,
                dex=dex,
                coin=coin,
                error=str(exc),
            )

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DBSession
from app.core.config import settings
from app.models.copy_execution import CopyAccountPosition, CopyExecutionOrder
from app.services.copy_engine.intent_service import ACTIVE_INTENT_STATUSES
from app.services.copy_engine.market_registry import MarketRegistry

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class CopyEngineHealthResponse(BaseModel):
    status: str
    live_enabled: bool
    registry_fresh: bool
    registry_age_seconds: float | None
    pending_unknown_intents: int
    dirty_positions: int
    oldest_reconcile_lag_seconds: float | None


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="0.2.0")


@router.get("/health/copy-engine-v2", response_model=CopyEngineHealthResponse)
async def copy_engine_health(db: DBSession) -> CopyEngineHealthResponse:
    registry = MarketRegistry().cached_snapshot()
    registry_age = registry.age_seconds if registry is not None else None
    registry_fresh = bool(
        registry is not None
        and registry_age is not None
        and registry_age <= settings.copy_engine_registry_stale_seconds
    )
    unknown_result = await db.execute(
        select(func.count(CopyExecutionOrder.id)).where(
            CopyExecutionOrder.status.in_(ACTIVE_INTENT_STATUSES)
        )
    )
    dirty_result = await db.execute(
        select(func.count(CopyAccountPosition.id)).where(
            CopyAccountPosition.status.in_(("dirty", "pending", "stalled"))
        )
    )
    oldest_result = await db.execute(
        select(func.min(CopyAccountPosition.updated_at)).where(
            CopyAccountPosition.status.in_(("dirty", "pending", "stalled"))
        )
    )
    oldest = oldest_result.scalar_one_or_none()
    lag = None
    if oldest is not None:
        lag = max(
            0.0,
            (datetime.now(tz=UTC).replace(tzinfo=None) - oldest).total_seconds(),
        )
    status = "ok" if registry_fresh or not settings.copy_engine_v2_live_enabled else "degraded"
    return CopyEngineHealthResponse(
        status=status,
        live_enabled=settings.copy_engine_v2_live_enabled,
        registry_fresh=registry_fresh,
        registry_age_seconds=registry_age,
        pending_unknown_intents=int(unknown_result.scalar_one()),
        dirty_positions=int(dirty_result.scalar_one()),
        oldest_reconcile_lag_seconds=lag,
    )

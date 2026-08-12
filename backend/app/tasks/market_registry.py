from app.core.logging import get_logger
from app.services.copy_engine.market_registry import MarketRegistry

logger = get_logger(__name__)


async def refresh_market_registry_async() -> None:
    try:
        await MarketRegistry().refresh()
    except Exception as exc:
        logger.error("copy_market_registry_refresh_failed", error=str(exc))

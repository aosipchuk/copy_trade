from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copy_execution import TraderMarketScope
from app.services.hyperliquid.info_client import HyperliquidInfoClient


async def discover_trader_dexes(
    db: AsyncSession,
    *,
    trader_id: int,
    trader_address: str,
    client: HyperliquidInfoClient | None = None,
) -> set[str]:
    """Discover HIP-3 scopes from recent fills; new scopes remain unpolled baseline."""
    hl = client or HyperliquidInfoClient()
    start_time = int(
        (datetime.now(tz=UTC) - timedelta(days=7)).timestamp() * 1000
    )
    fills = await hl.get_fills_by_time(
        trader_address,
        start_time=start_time,
        max_fills=2_000,
    )
    dexes = {""}
    for fill in fills:
        if ":" in fill.coin:
            dexes.add(fill.coin.split(":", 1)[0])
    for dex in sorted(dexes):
        if await db.get(TraderMarketScope, (trader_id, dex)) is None:
            db.add(
                TraderMarketScope(
                    trader_id=trader_id,
                    dex=dex,
                    discovery_source="fills" if dex else "default",
                    last_fill_time_ms=max(
                        (fill.time for fill in fills if fill.coin.startswith(f"{dex}:")),
                        default=None,
                    ),
                )
            )
    return dexes

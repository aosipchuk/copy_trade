import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.core.security import decode_access_token
from app.models.trader import Trader

router = APIRouter(tags=["websocket"])
logger = get_logger(__name__)

_POLL_INTERVAL = 2.5  # seconds between Redis snapshot reads


@router.websocket("/ws/traders/{trader_id}/positions")
async def trader_positions_ws(
    websocket: WebSocket,
    trader_id: int,
    token: str | None = None,
) -> None:
    """
    Stream position updates for a trader.
    Authenticate via ?token= query param (JWT).
    Sends the latest snapshot every ~2.5s if it changed.
    """
    # Authenticate
    if not token or not decode_access_token(token):
        await websocket.close(code=4001)
        return

    # Resolve trader HL address
    async with AsyncSessionFactory() as db:
        result = await db.execute(select(Trader).where(Trader.id == trader_id))
        trader = result.scalar_one_or_none()

    if trader is None:
        await websocket.close(code=4004)
        return

    await websocket.accept()
    snap_key = f"hl:snapshot:{trader.hl_address}"
    r = get_redis_client()
    last_sent: str | None = None

    try:
        while True:
            raw: str | None = await asyncio.to_thread(r.get, snap_key)
            if raw is not None and raw != last_sent:
                await websocket.send_text(raw)
                last_sent = raw
            await asyncio.sleep(_POLL_INTERVAL)
    except WebSocketDisconnect:
        logger.debug("ws_trader_disconnected", trader_id=trader_id)
    except Exception as exc:
        logger.warning("ws_trader_error", trader_id=trader_id, error=str(exc))
        await websocket.close(code=1011)

from fastapi import APIRouter

from app.api import (
    auth,
    demo,
    health,
    portfolios,
    subscriptions,
    traders,
    wallet,
    ws_traders,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(traders.export_router)
api_router.include_router(traders.router)
api_router.include_router(ws_traders.router)
api_router.include_router(wallet.router)
api_router.include_router(subscriptions.router)
api_router.include_router(portfolios.router)
api_router.include_router(demo.router)

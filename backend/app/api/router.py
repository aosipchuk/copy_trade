from fastapi import APIRouter

from app.api import (
    admin_traders,
    auth,
    demo,
    health,
    new_wallets,
    portfolio_billing,
    portfolio_subscriptions,
    portfolios,
    subscriptions,
    telegram,
    traders,
    wallet,
    ws_traders,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_traders.router)
api_router.include_router(telegram.router)
api_router.include_router(traders.export_router)
api_router.include_router(traders.router)
api_router.include_router(ws_traders.router)
api_router.include_router(wallet.router)
api_router.include_router(subscriptions.router)
api_router.include_router(new_wallets.router)
api_router.include_router(new_wallets.subscription_router)
api_router.include_router(new_wallets.admin_router)
api_router.include_router(portfolio_billing.router)
api_router.include_router(portfolio_subscriptions.router)
api_router.include_router(portfolios.router)
api_router.include_router(demo.router)

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.portfolio import (
    PortfolioBillingCheckoutCreate,
    PortfolioBillingCheckoutResponse,
    PortfolioBillingStatusResponse,
    PortfolioBillingWebhookResponse,
)
from app.services.portfolio.billing import (
    BillingConfigurationError,
    BillingProviderError,
    BillingSignatureError,
    create_portfolio_billing_checkout,
    get_portfolio_billing_status,
    handle_stripe_webhook,
)

router = APIRouter(
    prefix="/portfolio-subscriptions/billing",
    tags=["portfolio-billing"],
)


@router.get("/status", response_model=PortfolioBillingStatusResponse)
async def status_view(
    current_user: CurrentUser,
    db: DBSession,
    portfolio_id: int,
    active_version_id: int,
) -> PortfolioBillingStatusResponse:
    try:
        return await get_portfolio_billing_status(
            db, current_user, portfolio_id, active_version_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/checkout", response_model=PortfolioBillingCheckoutResponse)
async def checkout(
    body: PortfolioBillingCheckoutCreate,
    current_user: CurrentUser,
    db: DBSession,
) -> PortfolioBillingCheckoutResponse:
    try:
        return await create_portfolio_billing_checkout(db, current_user, body)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except BillingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except BillingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


@router.post("/webhook", response_model=PortfolioBillingWebhookResponse)
async def stripe_webhook(
    request: Request,
    db: DBSession,
    stripe_signature: str = Header(alias="Stripe-Signature"),
) -> PortfolioBillingWebhookResponse:
    payload = await request.body()
    try:
        return await handle_stripe_webhook(db, payload, stripe_signature)
    except BillingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except BillingSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

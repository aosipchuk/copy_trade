import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioVersion,
    UserPortfolioSubscription,
)
from app.models.user import User
from app.schemas.portfolio import (
    BillingProvider,
    PortfolioBillingCheckoutCreate,
    PortfolioBillingCheckoutResponse,
    PortfolioBillingStatusResponse,
    PortfolioBillingWebhookResponse,
    UserPortfolioSubscriptionDetailResponse,
)
from app.services.portfolio.subscription_lifecycle import (
    deactivate_portfolio_owned_subscriptions,
    lock_user_portfolio_subscription_slot,
)

logger = get_logger(__name__)

PAID_BILLING_STATUSES = {"active", "trialing"}
REBALANCE_BLOCKING_STATUSES = {"past_due", "paused", "canceled"}
STRIPE_SIGNATURE_TOLERANCE_SECONDS = 5 * 60

JsonDict = dict[str, Any]


class BillingConfigurationError(RuntimeError):
    pass


class BillingProviderError(RuntimeError):
    pass


class BillingSignatureError(ValueError):
    pass


class BillingPaymentRequiredError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _datetime_from_unix(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _provider(value: str | None) -> BillingProvider | None:
    if value in ("stripe", "admin_override"):
        return cast(BillingProvider, value)
    return None


def _object(value: object) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _metadata(value: object) -> dict[str, str]:
    metadata = _object(value)
    return {str(key): str(item) for key, item in metadata.items() if item is not None}


def _stripe_subscription_id(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _optional_str(value.get("id"))
    return None


def user_has_beta_override(user: User) -> bool:
    return user.telegram_id in settings.model_portfolio_beta_override_telegram_ids


def is_paid_billing_status(status: str | None) -> bool:
    return status in PAID_BILLING_STATUSES


def stripe_signature_payload(
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    *,
    now_ts: int | None = None,
    tolerance_seconds: int = STRIPE_SIGNATURE_TOLERANCE_SECONDS,
) -> JsonDict:
    if not webhook_secret:
        raise BillingConfigurationError("STRIPE_WEBHOOK_SECRET is not configured.")

    timestamp: int | None = None
    signatures: list[str] = []
    for part in signature_header.split(","):
        key, separator, value = part.partition("=")
        if not separator:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise BillingSignatureError(
                    "Invalid Stripe signature timestamp."
                ) from exc
        elif key == "v1" and value:
            signatures.append(value)

    if timestamp is None or not signatures:
        raise BillingSignatureError("Missing Stripe signature timestamp or v1 hash.")

    current_ts = int(time.time()) if now_ts is None else now_ts
    if abs(current_ts - timestamp) > tolerance_seconds:
        raise BillingSignatureError("Stripe webhook signature timestamp is stale.")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        webhook_secret.encode(), signed_payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise BillingSignatureError("Invalid Stripe webhook signature.")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BillingSignatureError("Invalid Stripe webhook JSON payload.") from exc
    if not isinstance(parsed, dict):
        raise BillingSignatureError("Invalid Stripe webhook event payload.")
    return cast(JsonDict, parsed)


async def _load_published_portfolio(
    db: AsyncSession,
    portfolio_id: int,
    active_version_id: int,
) -> tuple[ModelPortfolio, ModelPortfolioVersion]:
    result = await db.execute(
        select(ModelPortfolio, ModelPortfolioVersion)
        .join(
            ModelPortfolioVersion,
            ModelPortfolioVersion.portfolio_id == ModelPortfolio.id,
        )
        .where(
            ModelPortfolio.id == portfolio_id,
            ModelPortfolio.status == "active",
            ModelPortfolioVersion.id == active_version_id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("Published model portfolio version not found.")
    portfolio, version = row
    return portfolio, version


async def _latest_live_billing_subscription(
    db: AsyncSession,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
    *,
    include_canceled: bool,
) -> UserPortfolioSubscription | None:
    statement = (
        select(UserPortfolioSubscription)
        .where(
            UserPortfolioSubscription.user_id == user_id,
            UserPortfolioSubscription.portfolio_id == portfolio_id,
            UserPortfolioSubscription.active_version_id == active_version_id,
            UserPortfolioSubscription.is_demo.is_(False),
        )
        .order_by(
            UserPortfolioSubscription.created_at.desc(),
            UserPortfolioSubscription.id.desc(),
        )
        .limit(1)
    )
    if not include_canceled:
        statement = statement.where(UserPortfolioSubscription.status != "canceled")

    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def _load_detail(
    db: AsyncSession,
    user_id: int,
    subscription_id: int,
) -> UserPortfolioSubscriptionDetailResponse:
    from app.services.portfolio.activation import get_user_portfolio_subscription

    return await get_user_portfolio_subscription(db, user_id, subscription_id)


async def _detail_or_none(
    db: AsyncSession,
    subscription: UserPortfolioSubscription | None,
) -> UserPortfolioSubscriptionDetailResponse | None:
    if subscription is None:
        return None
    return await _load_detail(db, subscription.user_id, subscription.id)


async def get_portfolio_billing_status(
    db: AsyncSession,
    user: User,
    portfolio_id: int,
    active_version_id: int,
) -> PortfolioBillingStatusResponse:
    await _load_published_portfolio(db, portfolio_id, active_version_id)
    subscription = await _latest_live_billing_subscription(
        db,
        user.id,
        portfolio_id,
        active_version_id,
        include_canceled=True,
    )
    beta_override = user_has_beta_override(user)
    paid = beta_override or is_paid_billing_status(
        subscription.status if subscription else None
    )
    can_rebalance = paid and (
        beta_override
        or subscription is None
        or subscription.status not in REBALANCE_BLOCKING_STATUSES
    )

    if beta_override:
        message = "Beta billing override is active for this Telegram account."
    elif subscription is None:
        message = "Payment is required before live model portfolio activation."
    elif paid:
        message = "Billing is active for live model portfolio access."
    else:
        message = (
            f"Billing status '{subscription.status}' blocks live model portfolio "
            "activation."
        )

    return PortfolioBillingStatusResponse(
        portfolio_id=portfolio_id,
        active_version_id=active_version_id,
        paid=paid,
        can_activate_live=paid,
        can_rebalance=can_rebalance,
        beta_override=beta_override,
        provider=_provider(subscription.billing_provider if subscription else None),
        status=subscription.status if subscription else None,
        current_period_end=subscription.current_period_end if subscription else None,
        portfolio_subscription=await _detail_or_none(db, subscription),
        message=message,
    )


async def _create_stripe_checkout_session(
    subscription: UserPortfolioSubscription,
    data: PortfolioBillingCheckoutCreate,
) -> str:
    success_url = data.success_url or settings.stripe_checkout_success_url
    cancel_url = data.cancel_url or settings.stripe_checkout_cancel_url
    if not success_url or not cancel_url:
        raise BillingConfigurationError(
            "Stripe checkout success and cancel URLs must be configured."
        )

    form = {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(subscription.id),
        "line_items[0][price]": settings.stripe_portfolio_price_id,
        "line_items[0][quantity]": "1",
        "metadata[user_portfolio_subscription_id]": str(subscription.id),
        "metadata[user_id]": str(subscription.user_id),
        "metadata[portfolio_id]": str(subscription.portfolio_id),
        "metadata[active_version_id]": str(subscription.active_version_id),
        "subscription_data[metadata][user_portfolio_subscription_id]": str(
            subscription.id
        ),
        "subscription_data[metadata][user_id]": str(subscription.user_id),
        "subscription_data[metadata][portfolio_id]": str(subscription.portfolio_id),
        "subscription_data[metadata][active_version_id]": str(
            subscription.active_version_id
        ),
    }
    if subscription.billing_customer_id:
        form["customer"] = subscription.billing_customer_id

    headers = {
        "Authorization": f"Bearer {settings.stripe_api_key}",
        "Idempotency-Key": f"model-portfolio-billing-{subscription.id}",
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.stripe_api_url.rstrip("/"), timeout=15.0
        ) as client:
            response = await client.post(
                "/checkout/sessions", data=form, headers=headers
            )
    except httpx.HTTPError as exc:
        raise BillingProviderError("Stripe checkout request failed.") from exc

    if response.status_code >= 400:
        raise BillingProviderError(
            f"Stripe checkout request failed with status {response.status_code}: "
            f"{response.text[:200]}"
        )

    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("url"), str):
        raise BillingProviderError("Stripe checkout response did not include a URL.")
    return cast(str, body["url"])


async def create_portfolio_billing_checkout(
    db: AsyncSession,
    user: User,
    data: PortfolioBillingCheckoutCreate,
) -> PortfolioBillingCheckoutResponse:
    await _load_published_portfolio(db, data.portfolio_id, data.active_version_id)
    await lock_user_portfolio_subscription_slot(
        db,
        user_id=user.id,
        portfolio_id=data.portfolio_id,
        active_version_id=data.active_version_id,
        is_demo=False,
    )
    subscription = await _latest_live_billing_subscription(
        db,
        user.id,
        data.portfolio_id,
        data.active_version_id,
        include_canceled=False,
    )
    beta_override = user_has_beta_override(user)

    if subscription is None:
        subscription = UserPortfolioSubscription(
            user_id=user.id,
            portfolio_id=data.portfolio_id,
            active_version_id=data.active_version_id,
            status="active" if beta_override else "paused",
            is_demo=False,
            auto_rebalance=False,
            total_allocation_usd=data.total_allocation_usd,
            close_removed_positions=False,
            billing_provider="admin_override" if beta_override else "stripe",
        )
        db.add(subscription)
        await db.flush()
    else:
        subscription.total_allocation_usd = data.total_allocation_usd
        if beta_override and not is_paid_billing_status(subscription.status):
            subscription.status = "active"
            subscription.billing_provider = "admin_override"
        await db.flush()

    checkout_url: str | None = None
    provider_configured = settings.stripe_billing_configured
    if beta_override:
        message = "Beta override is active. Stripe checkout is not required."
    elif is_paid_billing_status(subscription.status):
        message = "Billing is already active. Stripe checkout is not required."
    elif provider_configured:
        checkout_url = await _create_stripe_checkout_session(subscription, data)
        message = "Stripe checkout session created."
    else:
        message = "Stripe billing is not configured for checkout."

    logger.info(
        "portfolio_billing_checkout_requested",
        user_id=user.id,
        portfolio_subscription_id=subscription.id,
        provider=subscription.billing_provider,
        provider_configured=provider_configured,
        checkout_url_created=checkout_url is not None,
    )

    detail = await _load_detail(db, user.id, subscription.id)
    billing_status = await get_portfolio_billing_status(
        db, user, data.portfolio_id, data.active_version_id
    )
    return PortfolioBillingCheckoutResponse(
        provider=_provider(subscription.billing_provider) or "stripe",
        provider_configured=provider_configured or beta_override,
        checkout_url=checkout_url,
        portfolio_subscription=detail,
        billing_status=billing_status,
        message=message,
    )


async def _find_subscription_for_webhook(
    db: AsyncSession,
    *,
    local_subscription_id: str | None,
    stripe_subscription_id: str | None,
) -> UserPortfolioSubscription | None:
    filters = []
    if local_subscription_id and local_subscription_id.isdigit():
        filters.append(UserPortfolioSubscription.id == int(local_subscription_id))
    if stripe_subscription_id:
        filters.append(
            UserPortfolioSubscription.billing_subscription_id == stripe_subscription_id
        )
    if not filters:
        return None

    result = await db.execute(select(UserPortfolioSubscription).where(or_(*filters)))
    return result.scalars().first()


def _map_stripe_subscription_status(status: str | None) -> str:
    if status in ("active", "trialing", "past_due", "paused", "canceled"):
        return status
    if status in ("incomplete_expired",):
        return "canceled"
    if status in ("incomplete", "unpaid"):
        return "past_due"
    return "past_due"


async def _apply_checkout_completed(
    db: AsyncSession,
    obj: JsonDict,
) -> int | None:
    metadata = _metadata(obj.get("metadata"))
    subscription_id = _stripe_subscription_id(obj.get("subscription"))
    local_subscription = await _find_subscription_for_webhook(
        db,
        local_subscription_id=metadata.get("user_portfolio_subscription_id")
        or _optional_str(obj.get("client_reference_id")),
        stripe_subscription_id=subscription_id,
    )
    if local_subscription is None:
        return None

    local_subscription.status = "active"
    local_subscription.billing_provider = "stripe"
    local_subscription.billing_customer_id = _optional_str(obj.get("customer"))
    local_subscription.billing_subscription_id = subscription_id
    await db.flush()
    return local_subscription.id


async def _apply_subscription_event(
    db: AsyncSession,
    obj: JsonDict,
    *,
    force_canceled: bool = False,
) -> int | None:
    metadata = _metadata(obj.get("metadata"))
    stripe_subscription_id = _optional_str(obj.get("id"))
    local_subscription = await _find_subscription_for_webhook(
        db,
        local_subscription_id=metadata.get("user_portfolio_subscription_id"),
        stripe_subscription_id=stripe_subscription_id,
    )
    if local_subscription is None:
        return None

    next_status = (
        "canceled"
        if force_canceled
        else _map_stripe_subscription_status(_optional_str(obj.get("status")))
    )
    local_subscription.status = next_status
    local_subscription.billing_provider = "stripe"
    local_subscription.billing_customer_id = _optional_str(obj.get("customer"))
    local_subscription.billing_subscription_id = stripe_subscription_id
    local_subscription.current_period_end = _datetime_from_unix(
        obj.get("current_period_end")
    )
    if next_status == "canceled" and local_subscription.canceled_at is None:
        local_subscription.canceled_at = _now()
    if next_status == "canceled" and not local_subscription.is_demo:
        await deactivate_portfolio_owned_subscriptions(
            db,
            local_subscription,
            close_positions=False,
        )
    await db.flush()
    return local_subscription.id


async def handle_stripe_webhook(
    db: AsyncSession,
    payload: bytes,
    signature_header: str,
) -> PortfolioBillingWebhookResponse:
    event = stripe_signature_payload(
        payload, signature_header, settings.stripe_webhook_secret
    )
    event_type = _optional_str(event.get("type")) or "unknown"
    data = _object(event.get("data"))
    obj = _object(data.get("object"))

    updated_subscription_id: int | None = None
    if event_type == "checkout.session.completed":
        updated_subscription_id = await _apply_checkout_completed(db, obj)
    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
    ):
        updated_subscription_id = await _apply_subscription_event(db, obj)
    elif event_type == "customer.subscription.deleted":
        updated_subscription_id = await _apply_subscription_event(
            db, obj, force_canceled=True
        )

    logger.info(
        "portfolio_billing_webhook_received",
        event_type=event_type,
        updated_subscription_id=updated_subscription_id,
    )
    return PortfolioBillingWebhookResponse(
        received=True,
        event_type=event_type,
        updated_subscription_id=updated_subscription_id,
    )


async def require_live_portfolio_billing(
    db: AsyncSession,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
) -> None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise LookupError("User not found.")

    status = await get_portfolio_billing_status(
        db, user, portfolio_id, active_version_id
    )
    if not status.can_activate_live:
        raise BillingPaymentRequiredError(status.message)


async def require_portfolio_rebalance_billing(
    db: AsyncSession,
    user_portfolio_subscription_id: int,
) -> None:
    result = await db.execute(
        select(UserPortfolioSubscription, User)
        .join(User, User.id == UserPortfolioSubscription.user_id)
        .where(UserPortfolioSubscription.id == user_portfolio_subscription_id)
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("Portfolio subscription not found.")

    subscription, user = row
    if subscription.is_demo or user_has_beta_override(user):
        return
    if is_paid_billing_status(subscription.status):
        return
    raise BillingPaymentRequiredError(
        f"Billing status '{subscription.status}' blocks portfolio rebalance."
    )

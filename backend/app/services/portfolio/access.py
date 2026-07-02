from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import UserPortfolioSubscription
from app.models.subscription import Subscription
from app.models.user import User
from app.services.portfolio.billing import PAID_BILLING_STATUSES, user_has_beta_override

JsonDict = dict[str, Any]

_IDENTITY_KEYS = {
    "trader_id",
    "peer_trader_id",
    "trader_address",
    "trader_display_name",
    "trader_name",
    "hl_address",
}
_TRADER_OBJECT_IDENTITY_KEYS = {"id", "address", "display_name"}


async def user_can_view_portfolio_trader_identities(
    db: AsyncSession,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
) -> bool:
    user = await db.get(User, user_id)
    if user is not None and user_has_beta_override(user):
        return True

    result = await db.execute(
        select(UserPortfolioSubscription.id)
        .where(
            UserPortfolioSubscription.user_id == user_id,
            UserPortfolioSubscription.portfolio_id == portfolio_id,
            UserPortfolioSubscription.active_version_id == active_version_id,
            UserPortfolioSubscription.is_demo.is_(False),
            UserPortfolioSubscription.status.in_(PAID_BILLING_STATUSES),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def user_can_view_subscription_trader_identity(
    db: AsyncSession,
    user_id: int,
    subscription: Subscription,
) -> bool:
    if (
        subscription.source_type != "model_portfolio"
        or not subscription.managed_by_portfolio
        or subscription.source_id is None
    ):
        return True

    result = await db.execute(
        select(UserPortfolioSubscription).where(
            UserPortfolioSubscription.id == subscription.source_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    portfolio_subscription = result.scalar_one_or_none()
    if portfolio_subscription is None:
        return False

    return await user_can_view_portfolio_trader_identities(
        db,
        user_id,
        portfolio_subscription.portfolio_id,
        portfolio_subscription.active_version_id,
    )


def redact_trader_identity_payload(
    value: object,
    *,
    parent_key: str | None = None,
) -> object:
    if isinstance(value, Mapping):
        redacted: JsonDict = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _IDENTITY_KEYS or (
                parent_key == "trader" and key_text in _TRADER_OBJECT_IDENTITY_KEYS
            ):
                redacted[key_text] = None
            else:
                redacted[key_text] = redact_trader_identity_payload(
                    item,
                    parent_key=key_text,
                )
        return redacted

    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            redact_trader_identity_payload(item, parent_key=parent_key)
            for item in value
        ]

    return value


def redacted_json_dict(value: object) -> JsonDict | None:
    if not isinstance(value, Mapping):
        return None
    redacted = redact_trader_identity_payload(value)
    return redacted if isinstance(redacted, dict) else None

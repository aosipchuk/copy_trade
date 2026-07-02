import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioRebalanceEvent,
    UserPortfolioItem,
    UserPortfolioSubscription,
)
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.models.user import User, UserAgent
from app.schemas.portfolio import (
    PortfolioRebalanceApplyResponse,
    PortfolioRebalanceDiffItem,
    PortfolioRebalanceEventResponse,
    PortfolioRebalancePreviewResponse,
    UserPortfolioSubscriptionDetailResponse,
    UserPortfolioSubscriptionUpdate,
)
from app.schemas.subscription import SubscriptionCreate
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary
from app.services.portfolio.access import (
    redact_trader_identity_payload,
    user_can_view_portfolio_trader_identities,
)
from app.services.portfolio.activation import (
    detect_manual_live_conflicts,
    get_user_portfolio_subscription,
)
from app.services.portfolio.billing import (
    BillingPaymentRequiredError,
    require_portfolio_rebalance_billing,
)
from app.services.portfolio.explanations import (
    allocation_source_facts,
    rebalance_rationale,
)
from app.services.risk_manager import check_portfolio_risk
from app.services.subscription_service import create_subscription

logger = get_logger(__name__)

JsonDict = dict[str, Any]
ACTIVE_REBALANCE_STATUSES = ("trialing", "active", "past_due", "paused")
BLOCKER_ACTIONS = {
    "blocked_by_user_conflict",
    "blocked_by_payment",
    "blocked_by_wallet",
    "failed_risk_check",
}


@dataclass(frozen=True)
class ManagedPortfolioItem:
    item: UserPortfolioItem
    subscription: Subscription
    trader: Trader


@dataclass(frozen=True)
class RebalanceContext:
    portfolio_subscription: UserPortfolioSubscription
    user: User
    portfolio: ModelPortfolio
    from_version: ModelPortfolioVersion
    to_version: ModelPortfolioVersion
    active_items: list[ManagedPortfolioItem]
    target_allocations: list[ModelPortfolioAllocation]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _float(value: object) -> float:
    return float(value)  # type: ignore[arg-type]


def _same_decimal(left: object, right: object, precision: str = "0.001") -> bool:
    return abs(_decimal(left) - _decimal(right)) <= Decimal(precision)


def _same_optional_decimal(left: object, right: object) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return _same_decimal(left, right)


def _same_string_list(left: list[str] | None, right: list[str] | None) -> bool:
    return sorted(left or []) == sorted(right or [])


def _total_allocation(value: float) -> Decimal:
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sorted_allocations(
    allocations: Iterable[ModelPortfolioAllocation],
) -> list[ModelPortfolioAllocation]:
    return sorted(
        allocations,
        key=lambda item: (_decimal(item.target_weight_pct), item.id),
        reverse=True,
    )


def _target_allocations(
    total_allocation_usd: float,
    allocations: list[ModelPortfolioAllocation],
) -> dict[int, Decimal]:
    total = _total_allocation(total_allocation_usd)
    if total <= Decimal("0"):
        raise ValueError("Portfolio total allocation must be positive.")
    if not allocations:
        raise ValueError("Target portfolio version has no allocations.")

    targets: dict[int, Decimal] = {}
    assigned = Decimal("0.00")
    for allocation in allocations[:-1]:
        target = (
            total * _decimal(allocation.target_weight_pct) / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        targets[allocation.id] = target
        assigned += target

    last = allocations[-1]
    targets[last.id] = (total - assigned).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if targets[last.id] <= Decimal("0"):
        raise ValueError("Portfolio allocation is too small for the target weights.")
    return targets


def _validate_target_allocations(allocations: list[ModelPortfolioAllocation]) -> None:
    if not allocations:
        raise ValueError("Current published portfolio version has no allocations.")

    total_weight = sum(
        (_decimal(allocation.target_weight_pct) for allocation in allocations),
        Decimal("0"),
    )
    if abs(total_weight - Decimal("100.000")) > Decimal("0.001"):
        raise ValueError(
            "Current published portfolio version weights must sum to 100%."
        )

    inactive = [
        allocation.trader_id
        for allocation in allocations
        if allocation.trader is None or not allocation.trader.is_active
    ]
    if inactive:
        raise ValueError(
            "Current published portfolio version contains inactive traders: "
            + ", ".join(str(trader_id) for trader_id in inactive)
        )


def _target_subscription_create(
    allocation: ModelPortfolioAllocation,
    target_allocation: Decimal,
    *,
    is_demo: bool,
) -> SubscriptionCreate:
    return SubscriptionCreate(
        trader_id=allocation.trader_id,
        max_allocation_usd=float(target_allocation),
        copy_ratio_pct=float(allocation.copy_ratio_pct),
        stop_loss_pct=float(allocation.stop_loss_pct),
        max_leverage=float(allocation.max_leverage),
        sizing_mode=allocation.sizing_mode,
        max_per_coin_usd=(
            float(allocation.max_per_coin_usd)
            if allocation.max_per_coin_usd is not None
            else None
        ),
        allowed_coins=(
            list(allocation.allowed_coins)
            if allocation.allowed_coins is not None
            else None
        ),
        is_demo=is_demo,
    )


async def _load_rebalance_context(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
) -> RebalanceContext:
    result = await db.execute(
        select(
            UserPortfolioSubscription,
            User,
            ModelPortfolio,
            ModelPortfolioVersion,
        )
        .join(User, User.id == UserPortfolioSubscription.user_id)
        .join(
            ModelPortfolio, ModelPortfolio.id == UserPortfolioSubscription.portfolio_id
        )
        .join(
            ModelPortfolioVersion,
            ModelPortfolioVersion.id == UserPortfolioSubscription.active_version_id,
        )
        .where(
            UserPortfolioSubscription.id == user_portfolio_subscription_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("Portfolio subscription not found.")

    portfolio_subscription, user, portfolio, from_version = row
    if portfolio_subscription.status not in ACTIVE_REBALANCE_STATUSES:
        raise ValueError("Canceled portfolio subscriptions cannot be rebalanced.")

    current_result = await db.execute(
        select(ModelPortfolioVersion)
        .where(
            ModelPortfolioVersion.portfolio_id == portfolio.id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
        .limit(1)
    )
    to_version = current_result.scalar_one_or_none()
    if to_version is None:
        raise LookupError("Current published portfolio version not found.")

    item_result = await db.execute(
        select(UserPortfolioItem, Subscription, Trader)
        .join(Subscription, Subscription.id == UserPortfolioItem.subscription_id)
        .join(Trader, Trader.id == UserPortfolioItem.trader_id)
        .where(
            UserPortfolioItem.user_portfolio_subscription_id
            == portfolio_subscription.id,
            UserPortfolioItem.status == "active",
            Subscription.user_id == user_id,
            Subscription.source_type == "model_portfolio",
            Subscription.source_id == portfolio_subscription.id,
            Subscription.managed_by_portfolio.is_(True),
        )
        .order_by(UserPortfolioItem.id.asc())
    )
    active_items = [
        ManagedPortfolioItem(item=item, subscription=subscription, trader=trader)
        for item, subscription, trader in item_result.all()
    ]
    if not active_items:
        raise ValueError("Portfolio subscription has no active generated items.")

    allocation_result = await db.execute(
        select(ModelPortfolioAllocation)
        .options(selectinload(ModelPortfolioAllocation.trader))
        .where(ModelPortfolioAllocation.version_id == to_version.id)
    )
    target_allocations = _sorted_allocations(allocation_result.scalars().all())
    _validate_target_allocations(target_allocations)

    return RebalanceContext(
        portfolio_subscription=portfolio_subscription,
        user=user,
        portfolio=portfolio,
        from_version=from_version,
        to_version=to_version,
        active_items=active_items,
        target_allocations=target_allocations,
    )


async def _wallet_ready_for_live(
    db: AsyncSession, user: User
) -> tuple[bool, str | None]:
    if not user.hl_address:
        return False, "HL wallet address required before live rebalance."

    result = await db.execute(
        select(UserAgent.id)
        .where(
            UserAgent.user_id == user.id,
            UserAgent.is_active.is_(True),
            UserAgent.approved_at.is_not(None),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is None:
        return False, "Active Hyperliquid agent required before live rebalance."

    return True, None


async def _fetch_margin_summary(user: User) -> MarginSummary:
    if not user.hl_address:
        raise ValueError("HL wallet address required before live rebalance.")
    try:
        hl = HyperliquidInfoClient()
        return await hl.get_account_summary(user.hl_address)
    except Exception as exc:
        logger.error(
            "portfolio_rebalance_equity_fetch_failed",
            user_id=user.id,
            error=str(exc),
        )
        raise ValueError("Failed to fetch HL account data - try again later") from exc


def _allocation_name(allocation: ModelPortfolioAllocation) -> str:
    trader = allocation.trader
    if trader is None:
        return f"Trader {allocation.trader_id}"
    return str(trader.display_name or trader.hl_address[:10])


def _item_name(item: ManagedPortfolioItem) -> str:
    return item.trader.display_name or item.trader.hl_address[:10]


def _trader_label(display_name: str | None, address: str) -> str:
    return display_name or address[:10]


def _item_source_facts(item: ManagedPortfolioItem) -> JsonDict:
    return {
        "current_item": {
            "user_portfolio_item_id": item.item.id,
            "subscription_id": item.subscription.id,
            "allocation_id": item.item.allocation_id,
            "trader_id": item.item.trader_id,
            "target_weight_pct": _float(item.item.target_weight_pct),
            "target_allocation_usd": _float(item.item.target_allocation_usd),
            "copy_ratio_pct": _float(item.subscription.copy_ratio_pct),
            "stop_loss_pct": _float(item.subscription.stop_loss_pct),
            "max_leverage": _float(item.subscription.max_leverage),
            "sizing_mode": item.subscription.sizing_mode,
            "max_per_coin_usd": (
                _float(item.subscription.max_per_coin_usd)
                if item.subscription.max_per_coin_usd is not None
                else None
            ),
            "allowed_coins": list(item.subscription.allowed_coins or []),
            "source_type": item.subscription.source_type,
            "managed_by_portfolio": item.subscription.managed_by_portfolio,
        },
        "trader": {
            "id": item.trader.id,
            "address": item.trader.hl_address,
            "display_name": item.trader.display_name,
        },
    }


def _target_source_facts(
    allocation: ModelPortfolioAllocation,
    target_allocation: Decimal | None,
) -> JsonDict:
    facts = allocation_source_facts(allocation)
    facts["target_allocation"] = {
        "allocation_id": allocation.id,
        "trader_id": allocation.trader_id,
        "target_weight_pct": _float(allocation.target_weight_pct),
        "target_allocation_usd": (
            float(target_allocation) if target_allocation is not None else None
        ),
        "copy_ratio_pct": _float(allocation.copy_ratio_pct),
        "stop_loss_pct": _float(allocation.stop_loss_pct),
        "max_leverage": _float(allocation.max_leverage),
        "sizing_mode": allocation.sizing_mode,
        "max_per_coin_usd": (
            _float(allocation.max_per_coin_usd)
            if allocation.max_per_coin_usd is not None
            else None
        ),
        "allowed_coins": list(allocation.allowed_coins or []),
    }
    return facts


def _merge_source_facts(*items: JsonDict) -> JsonDict:
    merged: JsonDict = {}
    for item in items:
        merged.update(item)
    return merged


def _pct_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        return f"{_float(value):.3f}%"
    except (TypeError, ValueError):
        return None


def _redacted_rebalance_message(data: Mapping[str, Any]) -> str:
    action = data.get("action")
    if action == "add_trader":
        to_weight = _pct_text(data.get("to_weight_pct"))
        if to_weight is None:
            return "Add a portfolio trader to the managed portfolio."
        return f"Add a portfolio trader at {to_weight} target weight."
    if action == "remove_trader":
        return "Remove a portfolio trader from the managed portfolio."
    if action == "change_weight":
        from_weight = _pct_text(data.get("from_weight_pct"))
        to_weight = _pct_text(data.get("to_weight_pct"))
        if from_weight is None or to_weight is None:
            return "Change a portfolio trader target weight."
        return (
            "Change a portfolio trader target weight from "
            f"{from_weight} to {to_weight}."
        )
    if action == "change_risk_settings":
        changed_fields = data.get("changed_fields")
        changed = (
            ", ".join(str(item) for item in changed_fields)
            if isinstance(changed_fields, list)
            else ""
        )
        return "Update a portfolio trader risk settings" + (
            f": {changed}." if changed else "."
        )
    if action == "blocked_by_user_conflict":
        return "Manual live subscription conflict blocks adding a portfolio trader."
    return str(data.get("message") or "This rebalance item affects the portfolio.")


def _redact_rebalance_diff_payload(data: Mapping[str, Any]) -> JsonDict:
    redacted = redact_trader_identity_payload(data)
    payload: JsonDict = dict(redacted if isinstance(redacted, Mapping) else data)
    payload["trader_id"] = None
    payload["trader_address"] = None
    payload["trader_display_name"] = None
    payload["message"] = _redacted_rebalance_message(payload)
    source_facts = payload.get("source_facts")
    payload["source_facts"] = source_facts if isinstance(source_facts, dict) else None
    return payload


def _redact_rebalance_diff_item(
    item: PortfolioRebalanceDiffItem,
) -> PortfolioRebalanceDiffItem:
    return PortfolioRebalanceDiffItem(
        **_redact_rebalance_diff_payload(item.model_dump())
    )


def _redact_rebalance_response(
    response: PortfolioRebalancePreviewResponse,
) -> PortfolioRebalancePreviewResponse:
    payload = response.model_dump()
    payload["diff"] = [_redact_rebalance_diff_item(item) for item in response.diff]
    return PortfolioRebalancePreviewResponse(**payload)


def _redact_rebalance_event_diff(value: object) -> JsonDict | None:
    if not isinstance(value, Mapping):
        return None
    payload: JsonDict = dict(value)
    raw_diff = payload.get("diff")
    if isinstance(raw_diff, list):
        payload["diff"] = [
            _redact_rebalance_diff_payload(item) if isinstance(item, Mapping) else item
            for item in raw_diff
        ]
    redacted = redact_trader_identity_payload(payload)
    return dict(redacted) if isinstance(redacted, Mapping) else None


async def _can_view_rebalance_trader_identity(
    db: AsyncSession,
    ctx: RebalanceContext,
) -> bool:
    return await user_can_view_portfolio_trader_identities(
        db,
        ctx.user.id,
        ctx.portfolio.id,
        ctx.portfolio_subscription.active_version_id,
    )


def _risk_setting_changes(
    item: ManagedPortfolioItem,
    allocation: ModelPortfolioAllocation,
) -> list[str]:
    subscription = item.subscription
    changed: list[str] = []
    if not _same_decimal(subscription.copy_ratio_pct, allocation.copy_ratio_pct):
        changed.append("copy_ratio_pct")
    if not _same_decimal(subscription.stop_loss_pct, allocation.stop_loss_pct):
        changed.append("stop_loss_pct")
    if not _same_decimal(subscription.max_leverage, allocation.max_leverage):
        changed.append("max_leverage")
    if subscription.sizing_mode != allocation.sizing_mode:
        changed.append("sizing_mode")
    if not _same_optional_decimal(
        subscription.max_per_coin_usd, allocation.max_per_coin_usd
    ):
        changed.append("max_per_coin_usd")
    if not _same_string_list(subscription.allowed_coins, allocation.allowed_coins):
        changed.append("allowed_coins")
    return changed


def _build_diff(ctx: RebalanceContext) -> list[PortfolioRebalanceDiffItem]:
    target_amounts = _target_allocations(
        float(ctx.portfolio_subscription.total_allocation_usd),
        ctx.target_allocations,
    )
    active_by_trader = {item.item.trader_id: item for item in ctx.active_items}
    target_by_trader = {
        allocation.trader_id: allocation for allocation in ctx.target_allocations
    }
    diff: list[PortfolioRebalanceDiffItem] = []

    for trader_id, item in sorted(active_by_trader.items()):
        if trader_id in target_by_trader:
            continue
        source_facts = _item_source_facts(item)
        diff.append(
            PortfolioRebalanceDiffItem(
                action="remove_trader",
                trader_id=trader_id,
                trader_address=item.trader.hl_address,
                trader_display_name=item.trader.display_name,
                subscription_id=item.subscription.id,
                from_allocation_id=item.item.allocation_id,
                from_weight_pct=_float(item.item.target_weight_pct),
                from_allocation_usd=_float(item.item.target_allocation_usd),
                message=f"Remove {_item_name(item)} from the managed portfolio.",
                rationale=rebalance_rationale(
                    "remove_trader",
                    source_facts=source_facts,
                ),
                source_facts=source_facts,
            )
        )

    for trader_id, allocation in sorted(target_by_trader.items()):
        trader = allocation.trader
        target_amount = target_amounts[allocation.id]
        active_item = active_by_trader.get(trader_id)
        if active_item is None:
            source_facts = _target_source_facts(allocation, target_amount)
            diff.append(
                PortfolioRebalanceDiffItem(
                    action="add_trader",
                    trader_id=trader_id,
                    trader_address=trader.hl_address if trader is not None else None,
                    trader_display_name=(
                        trader.display_name if trader is not None else None
                    ),
                    to_allocation_id=allocation.id,
                    to_weight_pct=_float(allocation.target_weight_pct),
                    to_allocation_usd=float(target_amount),
                    message=(
                        f"Add {_allocation_name(allocation)} at "
                        f"{float(allocation.target_weight_pct):.3f}% target weight."
                    ),
                    rationale=rebalance_rationale(
                        "add_trader",
                        source_facts=source_facts,
                    ),
                    source_facts=source_facts,
                )
            )
            continue

        old_weight = _float(active_item.item.target_weight_pct)
        new_weight = _float(allocation.target_weight_pct)
        old_amount = _float(active_item.item.target_allocation_usd)
        new_amount = float(target_amount)
        if abs(old_weight - new_weight) > 0.001 or abs(old_amount - new_amount) > 0.01:
            source_facts = _merge_source_facts(
                _item_source_facts(active_item),
                _target_source_facts(allocation, target_amount),
            )
            diff.append(
                PortfolioRebalanceDiffItem(
                    action="change_weight",
                    trader_id=trader_id,
                    trader_address=active_item.trader.hl_address,
                    trader_display_name=active_item.trader.display_name,
                    subscription_id=active_item.subscription.id,
                    from_allocation_id=active_item.item.allocation_id,
                    to_allocation_id=allocation.id,
                    from_weight_pct=old_weight,
                    to_weight_pct=new_weight,
                    from_allocation_usd=old_amount,
                    to_allocation_usd=new_amount,
                    message=(
                        f"Change {_item_name(active_item)} target weight from "
                        f"{old_weight:.3f}% to {new_weight:.3f}%."
                    ),
                    rationale=rebalance_rationale(
                        "change_weight",
                        source_facts=source_facts,
                    ),
                    source_facts=source_facts,
                )
            )

        changed_fields = _risk_setting_changes(active_item, allocation)
        if changed_fields:
            source_facts = _merge_source_facts(
                _item_source_facts(active_item),
                _target_source_facts(allocation, target_amount),
            )
            diff.append(
                PortfolioRebalanceDiffItem(
                    action="change_risk_settings",
                    trader_id=trader_id,
                    trader_address=active_item.trader.hl_address,
                    trader_display_name=active_item.trader.display_name,
                    subscription_id=active_item.subscription.id,
                    from_allocation_id=active_item.item.allocation_id,
                    to_allocation_id=allocation.id,
                    from_weight_pct=old_weight,
                    to_weight_pct=new_weight,
                    from_allocation_usd=old_amount,
                    to_allocation_usd=new_amount,
                    changed_fields=changed_fields,
                    message=(
                        f"Update {_item_name(active_item)} risk settings: "
                        + ", ".join(changed_fields)
                        + "."
                    ),
                    rationale=rebalance_rationale(
                        "change_risk_settings",
                        source_facts=source_facts,
                        changed_fields=changed_fields,
                    ),
                    source_facts=source_facts,
                )
            )

    if not diff:
        source_facts = {
            "from_version_id": ctx.from_version.id,
            "to_version_id": ctx.to_version.id,
            "active_item_count": len(ctx.active_items),
            "target_allocation_count": len(ctx.target_allocations),
        }
        diff.append(
            PortfolioRebalanceDiffItem(
                action="no_change",
                message="Portfolio subscription already matches the current version.",
                rationale=rebalance_rationale(
                    "no_change",
                    source_facts=source_facts,
                ),
                source_facts=source_facts,
            )
        )
    return diff


async def _preview_blockers(
    db: AsyncSession,
    ctx: RebalanceContext,
    diff: list[PortfolioRebalanceDiffItem],
) -> tuple[list[PortfolioRebalanceDiffItem], str | None]:
    actionable = [item for item in diff if item.action != "no_change"]
    if not actionable:
        return [], None

    if not ctx.portfolio_subscription.is_demo:
        try:
            await require_portfolio_rebalance_billing(db, ctx.portfolio_subscription.id)
        except BillingPaymentRequiredError as exc:
            source_facts = {
                "portfolio_subscription_id": ctx.portfolio_subscription.id,
                "is_demo": ctx.portfolio_subscription.is_demo,
                "status": ctx.portfolio_subscription.status,
            }
            return [
                PortfolioRebalanceDiffItem(
                    action="blocked_by_payment",
                    message=str(exc),
                    rationale=rebalance_rationale(
                        "blocked_by_payment",
                        source_facts=source_facts,
                    ),
                    source_facts=source_facts,
                )
            ], str(exc)

        ready, reason = await _wallet_ready_for_live(db, ctx.user)
        if not ready:
            message = reason or "Live rebalance requires a ready wallet and agent."
            source_facts = {
                "user_id": ctx.user.id,
                "has_hl_address": bool(ctx.user.hl_address),
            }
            return [
                PortfolioRebalanceDiffItem(
                    action="blocked_by_wallet",
                    message=message,
                    rationale=rebalance_rationale(
                        "blocked_by_wallet",
                        source_facts=source_facts,
                    ),
                    source_facts=source_facts,
                )
            ], message

        added_trader_ids = [
            item.trader_id for item in diff if item.action == "add_trader"
        ]
        conflicts = await detect_manual_live_conflicts(
            db,
            ctx.user.id,
            (trader_id for trader_id in added_trader_ids if trader_id is not None),
        )
        if conflicts:
            blocker_diff: list[PortfolioRebalanceDiffItem] = []
            for conflict in conflicts:
                trader_label = _trader_label(
                    conflict.trader_display_name,
                    conflict.trader_address,
                )
                blocker_diff.append(
                    PortfolioRebalanceDiffItem(
                        action="blocked_by_user_conflict",
                        trader_id=conflict.trader_id,
                        trader_address=conflict.trader_address,
                        trader_display_name=conflict.trader_display_name,
                        subscription_id=conflict.subscription_id,
                        message=(
                            "Manual live subscription conflict blocks adding "
                            f"{trader_label}."
                        ),
                        rationale=rebalance_rationale(
                            "blocked_by_user_conflict",
                            source_facts={
                                "conflict_subscription_id": conflict.subscription_id,
                                "trader_id": conflict.trader_id,
                                "is_demo": conflict.is_demo,
                            },
                        ),
                        source_facts={
                            "conflict_subscription_id": conflict.subscription_id,
                            "trader_id": conflict.trader_id,
                            "trader_address": conflict.trader_address,
                            "trader_display_name": conflict.trader_display_name,
                            "is_demo": conflict.is_demo,
                        },
                    )
                )
            return blocker_diff, "Manual live subscription conflict blocks rebalance."

    return [], None


async def preview_user_portfolio_rebalance(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
) -> PortfolioRebalancePreviewResponse:
    ctx = await _load_rebalance_context(db, user_id, user_portfolio_subscription_id)
    diff = _build_diff(ctx)
    blockers, blocker_message = await _preview_blockers(db, ctx, diff)
    if blockers:
        diff = [*diff, *blockers]

    has_action = any(
        item.action != "no_change" and item.action not in BLOCKER_ACTIONS
        for item in diff
    )
    status = (
        "blocked"
        if blocker_message is not None
        else "pending" if has_action else "up_to_date"
    )
    response = PortfolioRebalancePreviewResponse(
        user_portfolio_subscription_id=ctx.portfolio_subscription.id,
        portfolio_id=ctx.portfolio.id,
        portfolio_slug=ctx.portfolio.slug,
        portfolio_name=ctx.portfolio.name,
        from_version_id=ctx.from_version.id,
        from_version_no=ctx.from_version.version_no,
        to_version_id=ctx.to_version.id,
        to_version_no=ctx.to_version.version_no,
        status=status,
        can_apply=status == "pending",
        auto_rebalance=ctx.portfolio_subscription.auto_rebalance,
        close_removed_positions=ctx.portfolio_subscription.close_removed_positions,
        is_demo=ctx.portfolio_subscription.is_demo,
        total_allocation_usd=_float(ctx.portfolio_subscription.total_allocation_usd),
        diff=diff,
        blocker=blocker_message,
    )
    if await _can_view_rebalance_trader_identity(db, ctx):
        return response
    return _redact_rebalance_response(response)


async def update_user_portfolio_subscription_settings(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
    data: UserPortfolioSubscriptionUpdate,
) -> UserPortfolioSubscriptionDetailResponse:
    result = await db.execute(
        select(UserPortfolioSubscription).where(
            UserPortfolioSubscription.id == user_portfolio_subscription_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    portfolio_subscription = result.scalar_one_or_none()
    if portfolio_subscription is None:
        raise LookupError("Portfolio subscription not found.")
    if portfolio_subscription.status == "canceled":
        raise ValueError("Canceled portfolio subscriptions cannot be updated.")

    if data.auto_rebalance is not None:
        portfolio_subscription.auto_rebalance = data.auto_rebalance
    if data.close_removed_positions is not None:
        portfolio_subscription.close_removed_positions = data.close_removed_positions
    await db.flush()
    return await get_user_portfolio_subscription(
        db, user_id, user_portfolio_subscription_id
    )


def _idempotency_key(
    user_portfolio_subscription_id: int,
    from_version_id: int,
    to_version_id: int,
) -> str:
    return (
        "model-portfolio-rebalance:"
        f"{user_portfolio_subscription_id}:{from_version_id}:{to_version_id}"
    )


async def _load_event(
    db: AsyncSession,
    idempotency_key: str,
) -> PortfolioRebalanceEvent | None:
    result = await db.execute(
        select(PortfolioRebalanceEvent).where(
            PortfolioRebalanceEvent.idempotency_key == idempotency_key
        )
    )
    return result.scalar_one_or_none()


async def _load_latest_completed_event_for_target(
    db: AsyncSession,
    user_portfolio_subscription_id: int,
    to_version_id: int,
) -> PortfolioRebalanceEvent | None:
    result = await db.execute(
        select(PortfolioRebalanceEvent)
        .where(
            PortfolioRebalanceEvent.user_portfolio_subscription_id
            == user_portfolio_subscription_id,
            PortfolioRebalanceEvent.to_version_id == to_version_id,
            PortfolioRebalanceEvent.status == "completed",
        )
        .order_by(
            PortfolioRebalanceEvent.executed_at.desc(),
            PortfolioRebalanceEvent.id.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def _event_response(
    event: PortfolioRebalanceEvent,
    *,
    include_trader_identity: bool = True,
) -> PortfolioRebalanceEventResponse:
    response = PortfolioRebalanceEventResponse.model_validate(event)
    if include_trader_identity:
        return response
    payload = response.model_dump()
    payload["diff_json"] = _redact_rebalance_event_diff(response.diff_json)
    return PortfolioRebalanceEventResponse(**payload)


async def _validate_live_target_risk(
    db: AsyncSession,
    ctx: RebalanceContext,
    target_amounts: dict[int, Decimal],
    margin_summary: MarginSummary | None,
) -> None:
    if ctx.portfolio_subscription.is_demo:
        return
    if margin_summary is None:
        raise ValueError("HL account required for live rebalance.")

    for allocation in ctx.target_allocations:
        allowed, reason = await check_portfolio_risk(
            db,
            ctx.user.id,
            float(target_amounts[allocation.id]),
            float(allocation.max_leverage),
            margin_summary,
        )
        if not allowed:
            raise ValueError(reason)


def _apply_target_to_existing_item(
    item: ManagedPortfolioItem,
    allocation: ModelPortfolioAllocation,
    target_allocation: Decimal,
    target_version_id: int,
) -> None:
    subscription = item.subscription
    subscription.max_allocation_usd = float(target_allocation)
    subscription.copy_ratio_pct = float(allocation.copy_ratio_pct)
    subscription.stop_loss_pct = float(allocation.stop_loss_pct)
    subscription.max_leverage = float(allocation.max_leverage)
    subscription.sizing_mode = allocation.sizing_mode
    subscription.max_per_coin_usd = (
        float(allocation.max_per_coin_usd)
        if allocation.max_per_coin_usd is not None
        else None
    )
    subscription.allowed_coins = (
        list(allocation.allowed_coins) if allocation.allowed_coins is not None else None
    )
    subscription.source_version_id = target_version_id
    subscription.is_active = True

    item.item.portfolio_version_id = target_version_id
    item.item.allocation_id = allocation.id
    item.item.target_allocation_usd = float(target_allocation)
    item.item.target_weight_pct = float(allocation.target_weight_pct)
    item.item.status = "active"
    item.item.removed_at = None


async def _schedule_close_removed_position(
    portfolio_subscription: UserPortfolioSubscription,
    subscription_id: int,
) -> None:
    if (
        portfolio_subscription.is_demo
        or not portfolio_subscription.close_removed_positions
    ):
        return
    from app.tasks.execution_tasks import close_subscription_positions_async

    asyncio.create_task(
        close_subscription_positions_async(
            portfolio_subscription.user_id,
            subscription_id,
        )
    )


async def _apply_rebalance_mutations(
    db: AsyncSession,
    ctx: RebalanceContext,
) -> None:
    target_amounts = _target_allocations(
        float(ctx.portfolio_subscription.total_allocation_usd),
        ctx.target_allocations,
    )
    active_by_trader = {item.item.trader_id: item for item in ctx.active_items}
    target_by_trader = {
        allocation.trader_id: allocation for allocation in ctx.target_allocations
    }

    user_hl_address: str | None = None
    margin_summary: MarginSummary | None = None
    if not ctx.portfolio_subscription.is_demo:
        ready, reason = await _wallet_ready_for_live(db, ctx.user)
        if not ready:
            raise ValueError(reason or "Live rebalance requires a ready wallet.")
        user_hl_address = ctx.user.hl_address
        margin_summary = await _fetch_margin_summary(ctx.user)
        await _validate_live_target_risk(db, ctx, target_amounts, margin_summary)

    now = _now()
    for trader_id, item in active_by_trader.items():
        if trader_id in target_by_trader:
            continue
        item.item.status = "removed"
        item.item.removed_at = now
        item.subscription.is_active = False
        await _schedule_close_removed_position(
            ctx.portfolio_subscription, item.subscription.id
        )

    for allocation in ctx.target_allocations:
        target_allocation = target_amounts[allocation.id]
        active_item = active_by_trader.get(allocation.trader_id)
        if active_item is not None:
            _apply_target_to_existing_item(
                active_item,
                allocation,
                target_allocation,
                ctx.to_version.id,
            )
            continue

        subscription_response = await create_subscription(
            db,
            ctx.user.id,
            _target_subscription_create(
                allocation,
                target_allocation,
                is_demo=ctx.portfolio_subscription.is_demo,
            ),
            user_hl_address,
            source_type="model_portfolio",
            source_id=ctx.portfolio_subscription.id,
            source_version_id=ctx.to_version.id,
            managed_by_portfolio=True,
            margin_summary=margin_summary,
        )
        db.add(
            UserPortfolioItem(
                user_portfolio_subscription_id=ctx.portfolio_subscription.id,
                subscription_id=subscription_response.id,
                portfolio_version_id=ctx.to_version.id,
                allocation_id=allocation.id,
                trader_id=allocation.trader_id,
                target_allocation_usd=float(target_allocation),
                target_weight_pct=float(allocation.target_weight_pct),
                status="active",
            )
        )

    ctx.portfolio_subscription.active_version_id = ctx.to_version.id
    await db.flush()


def _action_count(
    diff: list[PortfolioRebalanceDiffItem],
    action: str,
) -> int:
    return sum(1 for item in diff if item.action == action)


async def _send_rebalance_notification(
    ctx: RebalanceContext,
    preview: PortfolioRebalancePreviewResponse,
) -> None:
    from app.services.notifications.telegram import (
        format_model_portfolio_rebalance_completed,
        send_trade_notification,
    )

    await send_trade_notification(
        ctx.user.telegram_id,
        format_model_portfolio_rebalance_completed(
            portfolio_name=ctx.portfolio.name,
            from_version_no=preview.from_version_no,
            to_version_no=preview.to_version_no,
            added_count=_action_count(preview.diff, "add_trader"),
            removed_count=_action_count(preview.diff, "remove_trader"),
            changed_count=(
                _action_count(preview.diff, "change_weight")
                + _action_count(preview.diff, "change_risk_settings")
            ),
        ),
    )


async def apply_user_portfolio_rebalance(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
    *,
    event_type: str = "user_apply",
) -> PortfolioRebalanceApplyResponse:
    preview = await preview_user_portfolio_rebalance(
        db, user_id, user_portfolio_subscription_id
    )
    key = _idempotency_key(
        user_portfolio_subscription_id,
        preview.from_version_id,
        preview.to_version_id,
    )
    event = await _load_event(db, key)
    if (
        event is None
        and not preview.can_apply
        and preview.from_version_id == preview.to_version_id
    ):
        event = await _load_latest_completed_event_for_target(
            db,
            user_portfolio_subscription_id,
            preview.to_version_id,
        )
    if event is not None and event.status == "completed":
        detail = await get_user_portfolio_subscription(
            db, user_id, user_portfolio_subscription_id
        )
        return PortfolioRebalanceApplyResponse(
            **preview.model_dump(),
            event=_event_response(
                event,
                include_trader_identity=detail.trader_details_visible,
            ),
            portfolio_subscription=detail,
        )
    if event is not None and event.status == "skipped" and not preview.can_apply:
        detail = await get_user_portfolio_subscription(
            db, user_id, user_portfolio_subscription_id
        )
        return PortfolioRebalanceApplyResponse(
            **preview.model_dump(),
            event=_event_response(
                event,
                include_trader_identity=detail.trader_details_visible,
            ),
            portfolio_subscription=detail,
        )

    if event is None:
        event = PortfolioRebalanceEvent(
            portfolio_id=preview.portfolio_id,
            from_version_id=preview.from_version_id,
            to_version_id=preview.to_version_id,
            user_portfolio_subscription_id=user_portfolio_subscription_id,
            event_type=event_type,
            status="running" if preview.can_apply else "skipped",
            diff_json=preview.model_dump(mode="json"),
            error_msg=preview.blocker,
            idempotency_key=key,
        )
        db.add(event)
    else:
        event.event_type = event_type
        event.status = "running" if preview.can_apply else "skipped"
        event.diff_json = preview.model_dump(mode="json")
        event.error_msg = preview.blocker
        event.executed_at = None
    await db.flush()

    if not preview.can_apply:
        event.status = "skipped"
        event.executed_at = _now()
        await db.flush()
        detail = await get_user_portfolio_subscription(
            db, user_id, user_portfolio_subscription_id
        )
        return PortfolioRebalanceApplyResponse(
            **preview.model_dump(),
            event=_event_response(
                event,
                include_trader_identity=detail.trader_details_visible,
            ),
            portfolio_subscription=detail,
        )

    ctx = await _load_rebalance_context(db, user_id, user_portfolio_subscription_id)
    try:
        await _apply_rebalance_mutations(db, ctx)
    except ValueError as exc:
        event.status = "failed"
        event.error_msg = str(exc)
        event.executed_at = _now()
        await db.flush()
        raise

    event.status = "completed"
    event.error_msg = None
    event.executed_at = _now()
    event.diff_json = {
        **preview.model_dump(mode="json"),
        "applied": True,
    }
    await db.flush()
    await _send_rebalance_notification(ctx, preview)
    detail = await get_user_portfolio_subscription(
        db, user_id, user_portfolio_subscription_id
    )
    logger.info(
        "portfolio_rebalance_completed",
        user_id=user_id,
        portfolio_subscription_id=user_portfolio_subscription_id,
        from_version_id=preview.from_version_id,
        to_version_id=preview.to_version_id,
    )
    return PortfolioRebalanceApplyResponse(
        **preview.model_dump(),
        event=_event_response(
            event,
            include_trader_identity=detail.trader_details_visible,
        ),
        portfolio_subscription=detail,
    )


async def list_user_portfolio_rebalance_events(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
    *,
    limit: int = 20,
) -> list[PortfolioRebalanceEventResponse]:
    result = await db.execute(
        select(UserPortfolioSubscription).where(
            UserPortfolioSubscription.id == user_portfolio_subscription_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    portfolio_subscription = result.scalar_one_or_none()
    if portfolio_subscription is None:
        raise LookupError("Portfolio subscription not found.")
    include_trader_identity = await user_can_view_portfolio_trader_identities(
        db,
        user_id,
        portfolio_subscription.portfolio_id,
        portfolio_subscription.active_version_id,
    )

    event_result = await db.execute(
        select(PortfolioRebalanceEvent)
        .where(
            PortfolioRebalanceEvent.user_portfolio_subscription_id
            == user_portfolio_subscription_id
        )
        .order_by(
            PortfolioRebalanceEvent.created_at.desc(),
            PortfolioRebalanceEvent.id.desc(),
        )
        .limit(limit)
    )
    return [
        _event_response(event, include_trader_identity=include_trader_identity)
        for event in event_result.scalars().all()
    ]

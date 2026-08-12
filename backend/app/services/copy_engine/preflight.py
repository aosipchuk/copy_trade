from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.copy_execution import (
    CopyAccountExecutionState,
    CopyExecutionOrder,
)
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.user import User, UserAgent
from app.schemas.copy_execution import (
    BlockingOrder,
    BlockingPosition,
    CopyPreflightResponse,
    PreflightCheck,
)
from app.services.copy_engine.account_state import (
    SUPPORTED_ACCOUNT_MODES,
    AccountStateReader,
)
from app.services.copy_engine.execution_state import utcnow_naive
from app.services.copy_engine.market_registry import MarketRegistry
from app.services.hyperliquid.info_client import HyperliquidInfoClient


@dataclass(frozen=True)
class PreflightContext:
    state: CopyAccountExecutionState | None
    agent_ready: bool


def _check(code: str, ok: bool, success: str, failure: str) -> PreflightCheck:
    return PreflightCheck(code=code, ok=ok, message=success if ok else failure)


async def _context(
    db: AsyncSession,
    user: User,
) -> PreflightContext:
    state = await db.get(CopyAccountExecutionState, user.id)
    agent_result = await db.execute(
        select(UserAgent.id).where(
            UserAgent.user_id == user.id,
            UserAgent.is_active.is_(True),
            UserAgent.approved_at.is_not(None),
        )
    )
    return PreflightContext(state=state, agent_ready=agent_result.first() is not None)


async def run_preflight(
    db: AsyncSession,
    user: User,
    *,
    persist: bool,
    registry: MarketRegistry | None = None,
    reader: AccountStateReader | None = None,
) -> CopyPreflightResponse:
    checked_at = datetime.now(tz=UTC)
    context = await _context(db, user)
    state = context.state
    account_address = state.account_address if state is not None else None
    checks: list[PreflightCheck] = []
    positions: list[BlockingPosition] = []
    orders: list[BlockingOrder] = []

    wallet_ok = bool(user.hl_address)
    checks.append(
        _check(
            "master_wallet",
            wallet_ok,
            "Master Hyperliquid wallet is configured.",
            "Complete Hyperliquid wallet setup first.",
        )
    )
    checks.append(
        _check(
            "agent",
            context.agent_ready,
            "Trading agent is active and approved.",
            "An active approved trading agent is required.",
        )
    )

    selected_ok = bool(
        state
        and state.master_address
        and account_address
        and user.hl_address
        and state.master_address.lower() == user.hl_address.lower()
    )
    checks.append(
        _check(
            "copy_account",
            selected_ok,
            "Copy account is selected for the current master wallet.",
            "Select a master account or an owned subaccount.",
        )
    )
    dedicated_ok = bool(state and state.dedicated_confirmed_at)
    checks.append(
        _check(
            "dedicated_account",
            dedicated_ok,
            "Dedicated-account rule is acknowledged.",
            "Acknowledge that this account is used only by CopyTrade.",
        )
    )
    checks.append(
        _check(
            "live_feature",
            settings.copy_engine_v2_live_enabled,
            "Copy Engine v2 live execution is enabled.",
            "Live execution is currently disabled by the operator.",
        )
    )

    account_snapshot = None
    registry_snapshot = None
    api_error: str | None = None
    if selected_ok and account_address is not None:
        try:
            registry_snapshot = await (registry or MarketRegistry()).get_snapshot()
            account_snapshot = await (reader or AccountStateReader()).read(
                account_address, registry_snapshot
            )
        except Exception:
            api_error = "Hyperliquid account or market state is unavailable."

    registry_ok = bool(
        registry_snapshot
        and registry_snapshot.markets
        and registry_snapshot.age_seconds <= settings.copy_engine_registry_stale_seconds
    )
    checks.append(
        _check(
            "market_registry",
            registry_ok,
            "Market metadata and prices are fresh.",
            api_error or "Market metadata is unavailable or stale.",
        )
    )

    mode = account_snapshot.mode if account_snapshot else None
    mode_ok = mode in SUPPORTED_ACCOUNT_MODES
    checks.append(
        _check(
            "account_mode",
            mode_ok,
            f"Account mode {mode} is supported.",
            "Only Standard and Unified account modes are supported.",
        )
    )

    if account_snapshot is not None:
        positions = [
            BlockingPosition(
                dex=dex,
                coin=position.coin,
                side=position.side,
                size=float(position.abs_size),
            )
            for dex, position in account_snapshot.positions
        ]
        orders = [
            BlockingOrder(
                dex=dex,
                coin=order.coin,
                side=order.side,
                size=float(order.size),
                oid=order.oid,
                cloid=order.cloid,
            )
            for dex, order in account_snapshot.open_orders
        ]
    checks.append(
        _check(
            "flat_positions",
            account_snapshot is not None and not positions,
            "Copy account has no open positions.",
            "Close every position on the selected copy account.",
        )
    )
    checks.append(
        _check(
            "open_orders",
            account_snapshot is not None and not orders,
            "Copy account has no open orders.",
            "Cancel every open order on the selected copy account.",
        )
    )

    legacy_result = await db.execute(
        select(UserTrade.id)
        .join(Subscription, UserTrade.subscription_id == Subscription.id)
        .where(
            Subscription.user_id == user.id,
            UserTrade.status.in_(("pending", "unknown")),
        )
        .limit(1)
    )
    no_legacy_ambiguity = legacy_result.first() is None
    checks.append(
        _check(
            "legacy_orders",
            no_legacy_ambiguity,
            "No unresolved legacy orders exist.",
            "A legacy order is pending or has unknown status.",
        )
    )

    v2_result = await db.execute(
        select(CopyExecutionOrder.id)
        .where(
            CopyExecutionOrder.user_id == user.id,
            CopyExecutionOrder.status.in_(
                ("pending", "submitted", "partial", "unknown")
            ),
        )
        .limit(1)
    )
    no_v2_ambiguity = v2_result.first() is None
    checks.append(
        _check(
            "v2_intents",
            no_v2_ambiguity,
            "No unresolved v2 execution intents exist.",
            "A v2 execution intent is still active or unknown.",
        )
    )

    equity = account_snapshot.equity_usd if account_snapshot else None
    equity_ok = equity is not None and equity >= 0
    checks.append(
        _check(
            "account_equity",
            equity_ok,
            "Copy-account collateral is readable.",
            "Copy-account collateral cannot be valued safely.",
        )
    )

    ok = all(check.ok for check in checks)
    if persist and state is not None:
        state.last_preflight_at = utcnow_naive()
        state.account_mode = mode
        state.version += 1
        if account_snapshot is not None and account_address is not None:
            try:
                fills = await HyperliquidInfoClient().get_fills(
                    account_address, limit=1
                )
            except Exception:
                fills = []
            if fills:
                state.fill_cursor_ms = max(fill.time for fill in fills)
            else:
                state.fill_cursor_ms = int(checked_at.timestamp() * 1000)
        if ok and state.status == "blocked":
            state.status = "paused"
            state.reason = "manual_resume_required"
            state.details = None
            state.cleared_at = utcnow_naive()

    return CopyPreflightResponse(
        ok=ok,
        account_mode=mode,
        equity_usd=float(equity) if equity is not None else None,
        checked_at=checked_at,
        checks=checks,
        positions=positions,
        open_orders=orders,
    )

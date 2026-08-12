from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.copy_execution import CopyAccountExecutionState
from app.models.user import UserAgent
from app.schemas.copy_execution import (
    CopyAccountOption,
    CopyAccountSelectionRequest,
    CopyAccountStateResponse,
    CopyPreflightResponse,
)
from app.services.copy_engine.execution_state import get_or_create_account_state
from app.services.copy_engine.locking import lock_user_account
from app.services.copy_engine.preflight import run_preflight
from app.services.hyperliquid.info_client import HyperliquidInfoClient

router = APIRouter(prefix="/copy-execution", tags=["copy-execution"])


async def _account_options(master_address: str) -> list[CopyAccountOption]:
    options = [
        CopyAccountOption(
            address=master_address.lower(),
            name="Master account",
            kind="master",
        )
    ]
    subaccounts = await HyperliquidInfoClient().get_subaccounts(master_address)
    options.extend(
        CopyAccountOption(
            address=subaccount.sub_account_user.lower(),
            name=subaccount.name,
            kind="subaccount",
        )
        for subaccount in subaccounts
    )
    return options


async def _state_response(
    state: CopyAccountExecutionState,
    options: list[CopyAccountOption],
) -> CopyAccountStateResponse:
    return CopyAccountStateResponse(
        master_address=state.master_address,
        account_address=state.account_address,
        vault_address=state.vault_address,
        account_mode=state.account_mode,
        status=state.status,
        reason=state.reason,
        dedicated_confirmed_at=state.dedicated_confirmed_at,
        options=options,
    )


@router.get("/account", response_model=CopyAccountStateResponse)
async def get_copy_account(
    current_user: CurrentUser,
    db: DBSession,
) -> CopyAccountStateResponse:
    if not current_user.hl_address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete Hyperliquid wallet setup first.",
        )
    options = await _account_options(current_user.hl_address)
    state = await get_or_create_account_state(db, current_user.id)
    return await _state_response(state, options)


@router.put("/account", response_model=CopyAccountStateResponse)
async def select_copy_account(
    body: CopyAccountSelectionRequest,
    current_user: CurrentUser,
    db: DBSession,
) -> CopyAccountStateResponse:
    if not current_user.hl_address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete Hyperliquid wallet setup first.",
        )
    if not body.dedicated_account_acknowledged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The dedicated-account acknowledgement is required.",
        )
    options = await _account_options(current_user.hl_address)
    option_by_address = {option.address.lower(): option for option in options}
    selected = option_by_address.get(body.account_address.lower())
    if selected is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected address is not owned by the master account.",
        )

    agent_result = await db.execute(
        select(UserAgent.id).where(
            UserAgent.user_id == current_user.id,
            UserAgent.is_active.is_(True),
            UserAgent.approved_at.is_not(None),
        )
    )
    if agent_result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active approved trading agent is required.",
        )

    await lock_user_account(db, current_user.id)
    state = await get_or_create_account_state(db, current_user.id)
    state.master_address = current_user.hl_address.lower()
    state.account_address = selected.address
    state.vault_address = selected.address if selected.kind == "subaccount" else None
    state.dedicated_confirmed_at = datetime.now(tz=UTC).replace(tzinfo=None)
    state.account_mode = None
    state.status = "paused"
    state.reason = "preflight_required"
    state.details = None
    state.version += 1
    return await _state_response(state, options)


@router.get("/preflight", response_model=CopyPreflightResponse)
async def get_preflight(
    current_user: CurrentUser,
    db: DBSession,
) -> CopyPreflightResponse:
    return await run_preflight(db, current_user, persist=False)


@router.post("/preflight", response_model=CopyPreflightResponse)
async def perform_preflight(
    current_user: CurrentUser,
    db: DBSession,
) -> CopyPreflightResponse:
    return await run_preflight(db, current_user, persist=True)

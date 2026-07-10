from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AdminUser, DBSession
from app.core.rate_limit import limiter
from app.schemas.trader import (
    AdminTraderImportRequest,
    AdminTraderImportResponse,
    TraderDetail,
)
from app.services.admin_trader_import import (
    InvalidHLAddressError,
    TraderImportFetchError,
    import_hl_trader_for_analysis,
)

router = APIRouter(prefix="/admin/traders", tags=["admin-traders"])


@router.post("/import", response_model=AdminTraderImportResponse)
@limiter.limit("6/minute")
async def import_trader(
    request: Request,
    body: AdminTraderImportRequest,
    current_user: AdminUser,
    db: DBSession,
) -> AdminTraderImportResponse:
    try:
        result = await import_hl_trader_for_analysis(db, body.hl_address)
    except InvalidHLAddressError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except TraderImportFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return AdminTraderImportResponse(
        status=result.status,
        message=result.message,
        trader=TraderDetail(
            id=result.trader.id,
            hl_address=result.trader.hl_address,
            display_name=result.trader.display_name,
            is_active=result.trader.is_active,
            last_seen_at=result.trader.last_seen_at,
            stats=result.stats,
        ),
        has_perp_activity=result.has_perp_activity,
    )

import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.services.hyperliquid.models import (
    AccountEquitySnapshot,
    ClearinghouseState,
    Fill,
    LeaderboardResponse,
    LeaderboardRow,
    MarginSummary,
    Meta,
    NonFundingLedgerUpdate,
    Position,
    SpotBalance,
    SpotClearinghouseState,
)
from app.services.hyperliquid.rate_limiter import (
    hl_priority_low,
    hl_rate_limiter,
    weight_for_payload,
)

logger = get_logger(__name__)

_STATS_LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
_TIMEOUT = httpx.Timeout(30.0)
_MAX_RETRY_AFTER_SEC = 30.0  # cap how long we honor a server-advised back-off
_USER_FILLS_BY_TIME_PAGE_LIMIT = 2_000
USER_FILLS_BY_TIME_MAX_AVAILABLE = 10_000
_USER_NON_FUNDING_LEDGER_PAGE_LIMIT = 2_000
USER_NON_FUNDING_LEDGER_MAX_AVAILABLE = 10_000


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) to a float."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def _backoff_on_429(resp: httpx.Response) -> None:
    """Sleep for the server-advised window on HTTP 429 before the error is raised.

    HL returns 429 when our weight budget is exceeded; pausing here (honoring
    ``Retry-After`` when present) lets the bucket refill instead of immediately
    re-firing and amplifying the storm. tenacity then retries the call.
    """
    if resp.status_code != 429:
        return
    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
    if retry_after is not None:
        await asyncio.sleep(min(retry_after, _MAX_RETRY_AFTER_SEC))


class HyperliquidInfoClient:
    """Async client for Hyperliquid read-only Info API."""

    def __init__(self, base_url: str | None = None) -> None:
        self._info_url = f"{base_url or settings.hl_api_url}/info"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _get(self, url: str) -> Any:
        # Leaderboard fetch is heavy; throttle it like a heavy info call.
        await hl_rate_limiter.acquire(20.0, hl_priority_low.get())
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            await _backoff_on_429(resp)
            resp.raise_for_status()
            return resp.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> Any:
        await hl_rate_limiter.acquire(
            weight_for_payload(payload), hl_priority_low.get()
        )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(self._info_url, json=payload)
            await _backoff_on_429(resp)
            resp.raise_for_status()
            return resp.json()

    async def get_leaderboard(self) -> list[LeaderboardRow]:
        """Fetch full leaderboard from stats-data endpoint (~37k traders)."""
        data = await self._get(_STATS_LEADERBOARD_URL)
        response = LeaderboardResponse.model_validate(data)
        logger.info("hl_leaderboard_fetched", count=len(response.leaderboard_rows))
        return response.leaderboard_rows

    async def get_positions(self, address: str) -> list[Position]:
        """Fetch open perp positions for a trader address."""
        data = await self._post({"type": "clearinghouseState", "user": address})
        state = ClearinghouseState.model_validate(data)
        return state.open_positions

    _MARGIN_CACHE_TTL: int = 30  # seconds

    async def get_account_summary(self, address: str) -> MarginSummary:
        """Fetch margin summary (equity, margin used) for a user account.

        Results are cached in Redis for 30 s to reduce HL API load when
        check_stop_losses runs every minute across many subscriptions.
        """
        from app.core.redis_client import get_redis_client

        cache_key = f"hl:margin:{address}"
        try:
            r = get_redis_client()
            cached = r.get(cache_key)
            if cached is not None:
                return MarginSummary.model_validate_json(cached)
        except Exception as cache_exc:
            logger.warning("margin_cache_read_failed", error=str(cache_exc))

        data = await self._post({"type": "clearinghouseState", "user": address})
        state = ClearinghouseState.model_validate(data)
        if state.margin_summary is None:
            raise ValueError(f"marginSummary missing in HL response for {address}")

        try:
            r = get_redis_client()
            r.setex(
                cache_key,
                self._MARGIN_CACHE_TTL,
                state.margin_summary.model_dump_json(),
            )
        except Exception as cache_exc:
            logger.warning("margin_cache_write_failed", error=str(cache_exc))

        return state.margin_summary

    async def get_spot_balances(self, address: str) -> list[SpotBalance]:
        """Fetch spot balances for a user address."""
        data = await self._post({"type": "spotClearinghouseState", "user": address})
        state = SpotClearinghouseState.model_validate(data)
        return state.balances

    async def get_account_equity_usd(self, address: str) -> AccountEquitySnapshot:
        """Return a conservative account-equity snapshot for discovery evidence."""
        summary, spot_balances = await asyncio.gather(
            self.get_account_summary(address),
            self.get_spot_balances(address),
        )
        spot_usdc = sum(
            (
                balance.total
                for balance in spot_balances
                if balance.coin.upper() == "USDC"
            ),
            start=summary.account_value * 0,
        )
        if spot_usdc > summary.account_value:
            balance = spot_usdc
            source = "spot_usdc_total"
        else:
            balance = summary.account_value
            source = "perp_account_value"
        return AccountEquitySnapshot(
            balance_usd=balance,
            balance_source=source,
            perp_account_value_usd=summary.account_value,
            spot_usdc_total=spot_usdc,
            evidence={
                "perp_account_value_usd": str(summary.account_value),
                "spot_usdc_total": str(spot_usdc),
                "balance_source": source,
            },
        )

    async def get_all_mids(self) -> dict[str, str]:
        """Fetch current mid prices for all active markets."""
        data: dict[str, str] = await self._post({"type": "allMids"})
        return data

    async def get_fills(self, address: str, limit: int | None = 50) -> list[Fill]:
        """Fetch recent trade fills for an address.

        Hyperliquid's ``userFills`` endpoint returns at most the latest 2,000
        fills, even when ``limit`` is ``None`` locally.
        """
        data: list[Any] = await self._post({"type": "userFills", "user": address})
        fills = [Fill.model_validate(f) for f in data]
        return fills if limit is None else fills[:limit]

    async def get_fills_by_time(
        self,
        address: str,
        *,
        start_time: int = 0,
        end_time: int | None = None,
        max_fills: int = USER_FILLS_BY_TIME_MAX_AVAILABLE,
    ) -> list[Fill]:
        """Fetch the fill history available via ``userFillsByTime``.

        Hyperliquid caps this endpoint at 2,000 fills per response and exposes
        only the 10,000 most recent fills. Pagination advances by timestamp.
        """
        if max_fills <= 0:
            return []

        fills: list[Fill] = []
        seen: set[tuple[int, str, int, str, str, str, str, str]] = set()
        next_start_time = start_time

        while len(fills) < max_fills:
            payload: dict[str, Any] = {
                "type": "userFillsByTime",
                "user": address,
                "startTime": next_start_time,
            }
            if end_time is not None:
                payload["endTime"] = end_time

            data: list[Any] = await self._post(payload)
            page = [Fill.model_validate(f) for f in data]
            if not page:
                break

            page.sort(key=lambda f: f.time)
            for fill in page:
                key = (
                    fill.time,
                    fill.coin,
                    fill.oid,
                    fill.side,
                    fill.dir,
                    str(fill.px),
                    str(fill.sz),
                    str(fill.closed_pnl),
                )
                if key in seen:
                    continue
                seen.add(key)
                fills.append(fill)
                if len(fills) >= max_fills:
                    break

            if len(page) < _USER_FILLS_BY_TIME_PAGE_LIMIT:
                break

            last_time = page[-1].time
            next_start_time = last_time + 1
            if end_time is not None and next_start_time > end_time:
                break

        return fills

    async def get_non_funding_ledger_updates(
        self,
        address: str,
        *,
        start_time: int,
        end_time: int | None = None,
        max_updates: int = USER_NON_FUNDING_LEDGER_MAX_AVAILABLE,
    ) -> list[NonFundingLedgerUpdate]:
        """Fetch non-funding ledger updates available for a known user.

        The endpoint is user-scoped, not a global funding-event feed. Pagination
        advances by timestamp and dedupes same-time/hash records.
        """
        if max_updates <= 0:
            return []

        updates: list[NonFundingLedgerUpdate] = []
        seen: set[tuple[int, str | None, str]] = set()
        next_start_time = start_time

        while len(updates) < max_updates:
            payload: dict[str, Any] = {
                "type": "userNonFundingLedgerUpdates",
                "user": address,
                "startTime": next_start_time,
            }
            if end_time is not None:
                payload["endTime"] = end_time

            data: list[Any] = await self._post(payload)
            page = [NonFundingLedgerUpdate.model_validate(item) for item in data]
            if not page:
                break

            page.sort(key=lambda item: item.time)
            added_from_page = 0
            for update in page:
                key = (update.time, update.hash, update.delta.type)
                if key in seen:
                    continue
                seen.add(key)
                updates.append(update)
                added_from_page += 1
                if len(updates) >= max_updates:
                    break

            if (
                len(page) < _USER_NON_FUNDING_LEDGER_PAGE_LIMIT
                or added_from_page == 0
            ):
                break

            last_time = page[-1].time
            next_start_time = last_time + 1
            if end_time is not None and next_start_time > end_time:
                break

        return updates

    async def get_meta(self) -> Meta:
        """Fetch market metadata: asset names, size decimals, max leverage."""
        data = await self._post({"type": "meta"})
        return Meta.model_validate(data)

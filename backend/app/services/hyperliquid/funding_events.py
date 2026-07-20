from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.services.hyperliquid.address import normalize_hl_address
from app.services.hyperliquid.info_client import HyperliquidInfoClient


class FundingEventProviderError(RuntimeError):
    """Base error for funding-event provider failures."""


class FundingEventProviderUnavailable(FundingEventProviderError):
    """Raised when no global provider is configured for discovery scans."""


class FundingEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    target_address: str = Field(alias="targetAddress")
    source_address: str | None = Field(None, alias="sourceAddress")
    amount_usdc: Decimal | None = Field(None, alias="amountUsdc")
    tx_hash: str | None = Field(None, alias="txHash")
    event_time: datetime = Field(alias="eventTime")
    event_type: str = Field(default="unknown", alias="eventType")
    raw_event: dict[str, Any] = Field(default_factory=dict, alias="rawEvent")

    @field_validator("target_address", mode="before")
    @classmethod
    def _normalize_target_address(cls, value: Any) -> str:
        return normalize_hl_address(str(value))

    @field_validator("source_address", mode="before")
    @classmethod
    def _normalize_optional_source(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        raw = str(value).strip()
        try:
            return normalize_hl_address(raw)
        except ValueError:
            return raw

    @field_validator("event_time", mode="before")
    @classmethod
    def _parse_event_time(cls, value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value
            return value.astimezone(UTC).replace(tzinfo=None)
        if isinstance(value, int | float):
            # Providers usually emit milliseconds. Seconds still parse safely.
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed
            return parsed.astimezone(UTC).replace(tzinfo=None)
        raise ValueError("event_time must be datetime, unix timestamp, or ISO string")


class FundingEventBatch(BaseModel):
    events: list[FundingEvent]
    next_cursor: str | None = None


class FundingEventProvider(ABC):
    @abstractmethod
    async def fetch_events_since(
        self,
        *,
        start_time: datetime,
        cursor: str | None,
        limit: int,
    ) -> FundingEventBatch:
        """Fetch global incoming funding events for discovery."""
        raise NotImplementedError

    @abstractmethod
    async def latest_incoming_for_address(
        self,
        address: str,
        *,
        before_time: datetime | None = None,
    ) -> FundingEvent | None:
        """Fetch the latest incoming funding event for a known address."""
        raise NotImplementedError


class HttpFundingEventProvider(FundingEventProvider):
    """Generic adapter for a HyperCore/Bridge2/indexer funding-event feed."""

    def __init__(self, url: str, api_key: str = "") -> None:
        self._url = url
        self._api_key = api_key

    async def fetch_events_since(
        self,
        *,
        start_time: datetime,
        cursor: str | None,
        limit: int,
    ) -> FundingEventBatch:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        params: dict[str, str | int] = {
            "since_ms": int(start_time.replace(tzinfo=UTC).timestamp() * 1000),
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        async with httpx.AsyncClient(
            timeout=settings.new_wallet_provider_timeout_seconds
        ) as client:
            response = await client.get(self._url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        batch = _parse_provider_payload(payload)
        return _filter_batch_since(batch, start_time=start_time, limit=limit)

    async def latest_incoming_for_address(
        self,
        address: str,
        *,
        before_time: datetime | None = None,
    ) -> FundingEvent | None:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        params: dict[str, str | int] = {
            "target_address": normalize_hl_address(address),
            "limit": 1,
        }
        if before_time is not None:
            params["before_ms"] = int(
                before_time.replace(tzinfo=UTC).timestamp() * 1000
            )
        async with httpx.AsyncClient(
            timeout=settings.new_wallet_provider_timeout_seconds
        ) as client:
            response = await client.get(self._url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        batch = _parse_provider_payload(payload)
        candidates = [
            event
            for event in batch.events
            if event.target_address == normalize_hl_address(address)
            and (before_time is None or event.event_time <= before_time)
        ]
        if not candidates:
            if _is_hypurrscan_url(self._url):
                return await LedgerFundingEventProvider().latest_incoming_for_address(
                    address,
                    before_time=before_time,
                )
            return None
        return max(candidates, key=lambda event: event.event_time)


class LedgerFundingEventProvider(FundingEventProvider):
    """Known-address adapter backed by userNonFundingLedgerUpdates."""

    def __init__(self, client: HyperliquidInfoClient | None = None) -> None:
        self._client = client or HyperliquidInfoClient()

    async def fetch_events_since(
        self,
        *,
        start_time: datetime,
        cursor: str | None,
        limit: int,
    ) -> FundingEventBatch:
        raise FundingEventProviderUnavailable(
            "HyperLiquid Info API has no global funding-event feed; configure "
            "NEW_WALLET_FUNDING_EVENTS_URL for discovery scans."
        )

    async def latest_incoming_for_address(
        self,
        address: str,
        *,
        before_time: datetime | None = None,
    ) -> FundingEvent | None:
        end_time = (
            int(before_time.replace(tzinfo=UTC).timestamp() * 1000)
            if before_time is not None
            else None
        )
        updates = await self._client.get_non_funding_ledger_updates(
            address,
            start_time=0,
            end_time=end_time,
        )
        incoming: list[FundingEvent] = []
        for update in updates:
            delta = update.delta
            source = _ledger_source_for_target(delta, address)
            if source is None and delta.type.lower() in _LEDGER_TRANSFER_TYPES:
                continue
            amount = delta.amount_usdc
            if amount is not None and amount <= 0:
                continue
            event_type = delta.type
            if event_type.lower() not in _LEDGER_FUNDING_TYPES:
                continue
            raw = update.model_dump(mode="json", by_alias=True)
            incoming.append(
                FundingEvent.model_validate(
                    {
                        "targetAddress": address,
                        "sourceAddress": source,
                        "amountUsdc": amount,
                        "txHash": update.hash,
                        "eventTime": update.time,
                        "eventType": event_type,
                        "rawEvent": raw,
                    }
                )
            )

        if not incoming:
            return None
        return max(incoming, key=lambda event: event.event_time)


def _parse_provider_payload(payload: Any) -> FundingEventBatch:
    if isinstance(payload, list):
        return FundingEventBatch(events=_parse_events(payload))
    if not isinstance(payload, dict):
        raise FundingEventProviderError(
            "Funding provider response must be a JSON object"
        )

    raw_events = payload.get("events") or payload.get("data") or []
    if not isinstance(raw_events, list):
        raise FundingEventProviderError("Funding provider events must be a JSON list")
    cursor = payload.get("next_cursor") or payload.get("nextCursor")
    return FundingEventBatch(
        events=_parse_events(raw_events),
        next_cursor=str(cursor) if cursor else None,
    )


def _parse_events(items: list[Any]) -> list[FundingEvent]:
    events: list[FundingEvent] = []
    for item in items:
        event = _parse_event(item)
        if event is not None:
            events.append(event)
    return events


def _parse_event(item: Any) -> FundingEvent | None:
    if not isinstance(item, dict):
        raise FundingEventProviderError("Funding event must be a JSON object")
    if "action" in item:
        return _parse_hypurrscan_event(item)
    raw = dict(item)
    normalized = {
        "targetAddress": _first_present(item, "target_address", "targetAddress"),
        "sourceAddress": _first_present(item, "source_address", "sourceAddress"),
        "amountUsdc": _first_present(item, "amount_usdc", "amountUsdc"),
        "txHash": _first_present(item, "tx_hash", "txHash"),
        "eventTime": _first_present(item, "event_time", "eventTime", "time"),
        "eventType": _first_present(item, "event_type", "eventType", "type")
        or "unknown",
        "rawEvent": raw,
    }
    return FundingEvent.model_validate(normalized)


_HYPURRSCAN_TRANSFER_TYPES = {"spotsend", "sendasset", "usdsend"}
_LEDGER_TRANSFER_TYPES = {
    "send",
    "spotsend",
    "spottransfer",
    "internaltransfer",
    "subaccounttransfer",
}
_LEDGER_FUNDING_TYPES = _LEDGER_TRANSFER_TYPES | {"deposit", "transfer"}


def _parse_hypurrscan_event(item: dict[str, Any]) -> FundingEvent | None:
    if item.get("error") is not None:
        return None
    action = item.get("action")
    if not isinstance(action, dict):
        return None

    event_type = str(action.get("type") or "").lower()
    if event_type not in _HYPURRSCAN_TRANSFER_TYPES:
        return None

    token = action.get("token")
    if not _is_usdc_token(token, event_type=event_type):
        return None

    source = item.get("user")
    target = action.get("destination")
    amount = action.get("amount") or action.get("usd")
    if not source or not target or amount is None:
        return None

    try:
        source_address = normalize_hl_address(str(source))
        target_address = normalize_hl_address(str(target))
    except ValueError:
        return None
    if (
        source_address == target_address
        or _is_reserved_hl_address(source_address)
        or _is_reserved_hl_address(target_address)
    ):
        return None

    return FundingEvent.model_validate(
        {
            "targetAddress": target_address,
            "sourceAddress": source_address,
            "amountUsdc": amount,
            "txHash": item.get("hash"),
            "eventTime": item.get("time"),
            "eventType": action.get("type") or "hypurrscan_transfer",
            "rawEvent": {
                "provider": "hypurrscan",
                **item,
            },
        }
    )


def _is_usdc_token(token: Any, *, event_type: str) -> bool:
    if event_type == "usdsend":
        return True
    if token is None:
        return False
    token_name = str(token).upper()
    return token_name == "USDC" or token_name.startswith("USDC:")


def _filter_batch_since(
    batch: FundingEventBatch,
    *,
    start_time: datetime,
    limit: int,
) -> FundingEventBatch:
    events = sorted(
        (event for event in batch.events if event.event_time >= start_time),
        key=lambda event: event.event_time,
    )
    if limit > 0:
        events = events[:limit]
    return FundingEventBatch(events=events, next_cursor=batch.next_cursor)


def _ledger_source_for_target(delta: Any, target_address: str) -> str | None:
    event_type = str(delta.type or "").lower()
    if event_type not in _LEDGER_TRANSFER_TYPES:
        return _non_reserved_source(delta.source_address)

    destination = (
        delta.destination
        or delta.to_address
        or delta.to_user
        or getattr(delta, "dest_user", None)
    )
    if destination:
        try:
            if normalize_hl_address(str(destination)) != normalize_hl_address(
                target_address
            ):
                return None
        except ValueError:
            return None
    return _non_reserved_source(delta.source_address)


def _non_reserved_source(source: str | None) -> str | None:
    if source is None:
        return None
    try:
        if _is_reserved_hl_address(source):
            return None
    except ValueError:
        return source
    return source


def _is_hypurrscan_url(url: str) -> bool:
    return "hypurrscan.io" in url.lower()


_RESERVED_HL_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x2000000000000000000000000000000000000000",
    "0x2222222222222222222222222222222222222222",
}


def _is_reserved_hl_address(address: str) -> bool:
    return normalize_hl_address(address) in _RESERVED_HL_ADDRESSES


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def get_funding_event_provider() -> FundingEventProvider:
    if settings.new_wallet_funding_events_url:
        return HttpFundingEventProvider(
            settings.new_wallet_funding_events_url,
            settings.new_wallet_funding_events_api_key,
        )
    return LedgerFundingEventProvider()

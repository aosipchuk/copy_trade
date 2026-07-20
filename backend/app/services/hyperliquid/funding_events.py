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

        return _parse_provider_payload(payload)

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
            source = delta.source_address
            amount = delta.amount_usdc
            if amount is not None and amount <= 0:
                continue
            event_type = delta.type
            if event_type.lower() not in {
                "deposit",
                "transfer",
                "internaltransfer",
                "accountclasstransfer",
                "spottransfer",
            }:
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
        return FundingEventBatch(events=[_parse_event(item) for item in payload])
    if not isinstance(payload, dict):
        raise FundingEventProviderError(
            "Funding provider response must be a JSON object"
        )

    raw_events = payload.get("events") or payload.get("data") or []
    if not isinstance(raw_events, list):
        raise FundingEventProviderError("Funding provider events must be a JSON list")
    cursor = payload.get("next_cursor") or payload.get("nextCursor")
    return FundingEventBatch(
        events=[_parse_event(item) for item in raw_events],
        next_cursor=str(cursor) if cursor else None,
    )


def _parse_event(item: Any) -> FundingEvent:
    if not isinstance(item, dict):
        raise FundingEventProviderError("Funding event must be a JSON object")
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

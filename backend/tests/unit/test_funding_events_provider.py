from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from app.services.hyperliquid.funding_events import (
    HttpFundingEventProvider,
    LedgerFundingEventProvider,
)

pytestmark = pytest.mark.asyncio


@respx.mock
async def test_http_provider_parses_hypurrscan_usdc_transfers() -> None:
    source = "0x" + "11" * 20
    target = "0x" + "22" * 20
    other = "0x" + "33" * 20
    route = respx.get("https://api.hypurrscan.io/transfers").mock(
        return_value=Response(
            200,
            json=[
                {
                    "time": 1_784_570_400_000,
                    "user": other,
                    "action": {
                        "type": "sendAsset",
                        "destination": target,
                        "token": "PURR",
                        "amount": "10",
                    },
                    "hash": "0xnonusdc",
                    "error": None,
                },
                {
                    "time": 1_784_570_410_000,
                    "user": source,
                    "action": {
                        "type": "sendAsset",
                        "destination": target,
                        "token": "USDC:0x6d1e7cde53ba9467b783cb7c530ce054",
                        "amount": "945.69",
                    },
                    "hash": "0xgood",
                    "error": None,
                },
                {
                    "time": 1_784_570_420_000,
                    "user": other,
                    "action": {
                        "type": "spotSend",
                        "destination": target,
                        "token": "USDC",
                        "amount": "100",
                    },
                    "hash": "0xfailed",
                    "error": "Insufficient balance",
                },
            ],
        )
    )

    provider = HttpFundingEventProvider("https://api.hypurrscan.io/transfers")
    batch = await provider.fetch_events_since(
        start_time=datetime.fromtimestamp(1_784_570_405, tz=UTC).replace(
            tzinfo=None
        ),
        cursor=None,
        limit=10,
    )

    assert route.called
    assert len(batch.events) == 1
    event = batch.events[0]
    assert event.target_address == target
    assert event.source_address == source
    assert float(event.amount_usdc or 0) == pytest.approx(945.69)
    assert event.tx_hash == "0xgood"
    assert event.raw_event["provider"] == "hypurrscan"


@respx.mock
async def test_http_provider_skips_reserved_hyperliquid_addresses() -> None:
    user = "0x" + "11" * 20
    reserved = "0x2000000000000000000000000000000000000000"
    respx.get("https://api.hypurrscan.io/transfers").mock(
        return_value=Response(
            200,
            json=[
                {
                    "time": 1_784_570_410_000,
                    "user": user,
                    "action": {
                        "type": "sendAsset",
                        "destination": reserved,
                        "token": "USDC",
                        "amount": "1000",
                    },
                    "hash": "0xreservedtarget",
                    "error": None,
                },
                {
                    "time": 1_784_570_420_000,
                    "user": reserved,
                    "action": {
                        "type": "sendAsset",
                        "destination": user,
                        "token": "USDC",
                        "amount": "1000",
                    },
                    "hash": "0xreservedsource",
                    "error": None,
                },
            ],
        )
    )

    provider = HttpFundingEventProvider("https://api.hypurrscan.io/transfers")
    batch = await provider.fetch_events_since(
        start_time=datetime.fromtimestamp(1_784_570_405, tz=UTC).replace(
            tzinfo=None
        ),
        cursor=None,
        limit=10,
    )

    assert batch.events == []


@respx.mock
async def test_ledger_provider_uses_incoming_transfer_source() -> None:
    target = "0x" + "aa" * 20
    source = "0x" + "bb" * 20
    other = "0x" + "cc" * 20
    respx.post("https://api.hyperliquid.xyz/info").mock(
        return_value=Response(
            200,
            json=[
                {
                    "time": 1_784_570_500_000,
                    "hash": "0xoutgoing",
                    "delta": {
                        "type": "send",
                        "user": target,
                        "destination": other,
                        "token": "USDC",
                        "amount": "50",
                        "usdcValue": "50",
                    },
                },
                {
                    "time": 1_784_570_400_000,
                    "hash": "0xincoming",
                    "delta": {
                        "type": "spotTransfer",
                        "user": source,
                        "destination": target,
                        "token": "USDC",
                        "amount": "1500",
                        "usdcValue": "1500",
                    },
                },
            ],
        )
    )

    event = await LedgerFundingEventProvider().latest_incoming_for_address(target)

    assert event is not None
    assert event.source_address == source
    assert event.target_address == target
    assert event.tx_hash == "0xincoming"
    assert float(event.amount_usdc or 0) == pytest.approx(1500)

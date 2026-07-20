import json

import pytest
import respx
from httpx import Response

from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import NonFundingLedgerUpdate

_INFO_URL = "https://api.hyperliquid.xyz/info"

pytestmark = pytest.mark.asyncio


def test_non_funding_ledger_update_parses_unknown_delta_fields() -> None:
    payload = {
        "time": 1_720_000_000_000,
        "hash": "0xhash",
        "delta": {
            "type": "deposit",
            "usdc": "123.45",
            "from": "0x" + "11" * 20,
            "extraProviderField": "kept",
        },
    }

    update = NonFundingLedgerUpdate.model_validate(payload)

    assert update.delta.type == "deposit"
    assert update.delta.amount_usdc is not None
    assert float(update.delta.amount_usdc) == pytest.approx(123.45)
    assert update.delta.source_address == "0x" + "11" * 20
    assert update.delta.model_extra is not None
    assert update.delta.model_extra["extraProviderField"] == "kept"


@respx.mock
async def test_get_non_funding_ledger_updates_paginates() -> None:
    first_page = [
        {
            "time": 1_720_000_000_000 + idx,
            "hash": f"0x{idx}",
            "delta": {"type": "deposit", "usdc": "1", "from": "0x" + "11" * 20},
        }
        for idx in range(2_000)
    ]
    second_page = [
        {
            "time": 1_720_000_010_000,
            "hash": "0xlast",
            "delta": {"type": "deposit", "usdc": "1", "from": "0x" + "22" * 20},
        }
    ]
    route = respx.post(_INFO_URL).mock(
        side_effect=[Response(200, json=first_page), Response(200, json=second_page)]
    )

    updates = await HyperliquidInfoClient().get_non_funding_ledger_updates(
        "0x" + "aa" * 20,
        start_time=0,
        max_updates=2_001,
    )

    assert len(updates) == 2_001
    first_payload = json.loads(route.calls[0].request.content)
    second_payload = json.loads(route.calls[1].request.content)
    assert first_payload["type"] == "userNonFundingLedgerUpdates"
    assert second_payload["startTime"] == 1_720_000_001_999 + 1


@respx.mock
async def test_get_account_equity_uses_larger_spot_usdc_snapshot() -> None:
    state = {
        "assetPositions": [],
        "marginSummary": {
            "accountValue": "1000",
            "totalMarginUsed": "0",
            "totalRawUsd": "1000",
        },
    }
    spot = {"balances": [{"coin": "USDC", "total": "2000", "hold": "0"}]}
    respx.post(_INFO_URL).mock(
        side_effect=[Response(200, json=state), Response(200, json=spot)]
    )

    snapshot = await HyperliquidInfoClient().get_account_equity_usd(
        "0x" + "aa" * 20
    )

    assert float(snapshot.balance_usd) == pytest.approx(2000)
    assert snapshot.balance_source == "spot_usdc_total"

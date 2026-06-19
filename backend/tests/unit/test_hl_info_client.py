import pytest
import respx
from httpx import Response

from app.services.hyperliquid.info_client import HyperliquidInfoClient

_LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
_INFO_URL = "https://api.hyperliquid.xyz/info"

_SAMPLE_LEADERBOARD = {
    "leaderboardRows": [
        {
            "ethAddress": "0xabc",
            "accountValue": "10000.00",
            "displayName": "Alpha",
            "windowPerformances": [
                ["day", {"pnl": "100.0", "roi": "0.01", "vlm": "50000.0"}],
                ["week", {"pnl": "500.0", "roi": "0.05", "vlm": "250000.0"}],
            ],
            "prize": 0,
        }
    ]
}

_SAMPLE_STATE = {
    "assetPositions": [
        {
            "position": {
                "coin": "BTC",
                "szi": "0.01",
                "entryPx": "67000.0",
                "unrealizedPnl": "10.0",
                "leverage": {"type": "cross", "value": 10},
            },
            "type": "oneWay",
        },
        {
            "position": {
                "coin": "ETH",
                "szi": "0.0",  # closed — should be excluded
                "entryPx": "3500.0",
                "unrealizedPnl": "0.0",
                "leverage": {"type": "cross", "value": 5},
            },
            "type": "oneWay",
        },
    ]
}


class TestHyperliquidInfoClient:
    @pytest.fixture
    def client(self) -> HyperliquidInfoClient:
        return HyperliquidInfoClient()

    @respx.mock
    async def test_get_leaderboard_parses_rows(
        self, client: HyperliquidInfoClient
    ) -> None:
        respx.get(_LEADERBOARD_URL).mock(
            return_value=Response(200, json=_SAMPLE_LEADERBOARD)
        )

        rows = await client.get_leaderboard()

        assert len(rows) == 1
        assert rows[0].eth_address == "0xabc"
        assert rows[0].display_name == "Alpha"
        week_perf = rows[0].get_perf("week")
        assert week_perf is not None
        assert float(week_perf.roi) == pytest.approx(0.05)

    @respx.mock
    async def test_get_positions_filters_closed(
        self, client: HyperliquidInfoClient
    ) -> None:
        respx.post(_INFO_URL).mock(return_value=Response(200, json=_SAMPLE_STATE))

        positions = await client.get_positions("0xabc")

        assert len(positions) == 1
        assert positions[0].coin == "BTC"
        assert positions[0].side == "long"

    @respx.mock
    async def test_get_all_mids_returns_dict(
        self, client: HyperliquidInfoClient
    ) -> None:
        mids = {"BTC": "67000.0", "ETH": "3500.0"}
        respx.post(_INFO_URL).mock(return_value=Response(200, json=mids))

        result = await client.get_all_mids()

        assert result["BTC"] == "67000.0"

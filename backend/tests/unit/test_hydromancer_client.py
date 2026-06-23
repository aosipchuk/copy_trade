import pytest
import respx
from httpx import Response

from app.services.hydromancer.client import HydromancerClient

_BASE_URL = "https://test.hydromancer.xyz"
_INFO_URL = f"{_BASE_URL}/info"

_PAGE_ONE = {
    "users": [
        {"user": "0xAAA", "humanScore": 85, "totalPnl": 5000.0, "totalTrades": 120, "daysActive": 180, "volumeTraded": 1_000_000.0},
        {"user": "0xBBB", "humanScore": 42, "totalPnl": -100.0, "totalTrades": 30, "daysActive": 20, "volumeTraded": 50_000.0},
    ],
    "total": 3,
    "limit": 2,
    "offset": 0,
}

_PAGE_TWO = {
    "users": [
        {"user": "0xCCC", "humanScore": 70, "totalPnl": 1000.0, "totalTrades": 60, "daysActive": 90, "volumeTraded": 200_000.0},
    ],
    "total": 3,
    "limit": 2,
    "offset": 2,
}

_EMPTY = {"users": [], "total": 0, "limit": 1000, "offset": 0}


@pytest.fixture
def client() -> HydromancerClient:
    return HydromancerClient(api_key="test-key", base_url=_BASE_URL)


class TestHydromancerClientGetHumanScores:
    @pytest.mark.asyncio
    @respx.mock
    async def test_paginates_until_total_reached(self, client: HydromancerClient) -> None:
        respx.post(_INFO_URL).side_effect = [
            Response(200, json=_PAGE_ONE),
            Response(200, json=_PAGE_TWO),
        ]

        users = await client.get_human_scores(window="all", min_human_score=0)

        assert len(users) == 3
        assert users[0].user == "0xAAA"
        assert users[0].human_score == 85
        assert users[2].user == "0xCCC"

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_empty_list_when_no_users(self, client: HydromancerClient) -> None:
        respx.post(_INFO_URL).return_value = Response(200, json=_empty_page())

        users = await client.get_human_scores()

        assert users == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_authorization_header(self, client: HydromancerClient) -> None:
        respx.post(_INFO_URL).return_value = Response(200, json=_empty_page())

        await client.get_human_scores()

        sent_headers = respx.calls.last.request.headers
        assert sent_headers["Authorization"] == "Bearer test-key"

    @pytest.mark.asyncio
    @respx.mock
    async def test_passes_window_and_min_score_in_payload(self, client: HydromancerClient) -> None:
        import json

        respx.post(_INFO_URL).return_value = Response(200, json=_empty_page())

        await client.get_human_scores(window="month", min_human_score=50)

        body = json.loads(respx.calls.last.request.content)
        assert body["window"] == "month"
        assert body["minHumanScore"] == 50

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_http_error(self, client: HydromancerClient) -> None:
        respx.post(_INFO_URL).return_value = Response(500, json={"error": "server error"})

        with pytest.raises(Exception):
            await client.get_human_scores()


def _empty_page() -> dict:
    return {"users": [], "total": 0, "limit": 1000, "offset": 0}

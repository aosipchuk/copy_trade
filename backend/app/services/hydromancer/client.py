from typing import Any, cast

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.services.hydromancer.models import (
    HydromancerLeaderboardResponse,
    HydromancerUser,
)

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(30.0)
_PAGE_SIZE = 1000


class HydromancerClient:
    """Client for the Hydromancer analytics API."""

    def __init__(self, api_key: str, base_url: str = "") -> None:
        self._base_url = base_url or settings.hydromancer_api_url
        self._headers = {"Authorization": f"Bearer {api_key}"}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> dict[str, object]:
        async with httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=_TIMEOUT
        ) as client:
            resp = await client.post("/info", json=payload)
            resp.raise_for_status()
            data: object = resp.json()
            if not isinstance(data, dict):
                raise ValueError("Hydromancer API response must be a JSON object")
            return cast(dict[str, object], data)

    async def get_human_scores(
        self,
        window: str = "all",
        min_human_score: int = 0,
    ) -> list[HydromancerUser]:
        """Fetch all traders with their human scores, paginating automatically."""
        results: list[HydromancerUser] = []
        offset = 0

        while True:
            data = await self._post(
                {
                    "type": "userPnlLeaderboard",
                    "window": window,
                    "minHumanScore": min_human_score,
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                }
            )
            page = HydromancerLeaderboardResponse.model_validate(data)
            results.extend(page.users)

            logger.debug(
                "hydromancer_page_fetched",
                offset=offset,
                count=len(page.users),
                total=page.total,
            )

            if offset + len(page.users) >= page.total or not page.users:
                break
            offset += _PAGE_SIZE

        logger.info("hydromancer_scores_fetched", count=len(results))
        return results

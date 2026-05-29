import pytest
from httpx import AsyncClient


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_response_structure(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"status", "version"}

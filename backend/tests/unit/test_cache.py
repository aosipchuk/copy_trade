from unittest.mock import patch

import pytest

from app.core import cache
from app.core.cache import cached_json_stale_on_error


class FakeRedis:
    """In-memory stand-in for the sync Redis client (no TTL enforcement)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


class TestCachedJsonStaleOnError:
    async def test_fresh_hit_skips_producer(self) -> None:
        fake = FakeRedis()
        calls = 0

        async def producer() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"v": 1}

        with patch.object(cache, "get_redis_client", return_value=fake):
            first = await cached_json_stale_on_error("k", 30, 3600, producer)
            second = await cached_json_stale_on_error("k", 30, 3600, producer)

        assert first == {"v": 1}
        assert second == {"v": 1}
        assert calls == 1  # second call served from fresh cache

    async def test_serves_stale_when_producer_fails(self) -> None:
        fake = FakeRedis()

        async def good_producer() -> dict[str, int]:
            return {"v": 42}

        async def failing_producer() -> dict[str, int]:
            raise RuntimeError("429 Too Many Requests")

        with patch.object(cache, "get_redis_client", return_value=fake):
            await cached_json_stale_on_error("k", 30, 3600, good_producer)
            # Expire the fresh layer; only the stale layer remains.
            fake.delete("k")
            result = await cached_json_stale_on_error("k", 30, 3600, failing_producer)

        assert result == {"v": 42}  # last good value served despite the failure

    async def test_raises_when_no_stale_available(self) -> None:
        fake = FakeRedis()

        async def failing_producer() -> dict[str, int]:
            raise RuntimeError("boom")

        with (
            patch.object(cache, "get_redis_client", return_value=fake),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await cached_json_stale_on_error("k", 30, 3600, failing_producer)

    async def test_writes_both_fresh_and_stale_layers(self) -> None:
        fake = FakeRedis()

        async def producer() -> dict[str, int]:
            return {"v": 7}

        with patch.object(cache, "get_redis_client", return_value=fake):
            await cached_json_stale_on_error("k", 30, 3600, producer)

        assert "k" in fake.store
        assert "k:stale" in fake.store

from typing import Protocol, cast

import redis

from app.core.config import settings


class RedisClient(Protocol):
    """Synchronous Redis surface used by the application."""

    def get(self, name: str) -> str | None: ...

    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None: ...

    def setex(self, name: str, time: int, value: str) -> bool: ...

    def delete(self, *names: str) -> int: ...

    def exists(self, *names: str) -> int: ...

    def incrby(self, name: str, amount: int = 1) -> int: ...


def get_redis_client() -> RedisClient:
    client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    return cast(RedisClient, client)

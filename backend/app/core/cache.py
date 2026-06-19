import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.redis_client import get_redis_client

T = TypeVar("T")


async def cached_json(
    key: str,
    ttl: int,
    producer: Callable[[], Awaitable[T]],
) -> T:
    """
    Read-through JSON cache backed by Redis.
    Wraps sync Redis calls in asyncio.to_thread to avoid blocking the event loop.
    """
    r = get_redis_client()
    raw: str | None = await asyncio.to_thread(r.get, key)
    if raw is not None:
        return json.loads(raw)  # type: ignore[no-any-return]

    result = await producer()
    serialized = json.dumps(result)
    await asyncio.to_thread(r.setex, key, ttl, serialized)
    return result


async def cache_delete(key: str) -> None:
    r = get_redis_client()
    await asyncio.to_thread(r.delete, key)


async def cache_get_raw(key: str) -> str | None:
    r = get_redis_client()
    return await asyncio.to_thread(r.get, key)

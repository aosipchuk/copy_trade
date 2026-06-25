import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import get_logger
from app.core.redis_client import get_redis_client

T = TypeVar("T")

logger = get_logger(__name__)


def _sanitize_floats(obj: object) -> object:
    """Recursively replace NaN/Inf with None so json.dumps never raises."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_floats(v) for v in obj)
    return obj


async def cached_json(
    key: str,
    ttl: int,
    producer: Callable[[], Awaitable[T]],
) -> T:
    """
    Read-through JSON cache backed by Redis.
    Wraps sync Redis calls in asyncio.to_thread to avoid blocking the event loop.
    Falls back to uncached result if Redis is unavailable.
    """
    try:
        r = get_redis_client()
        raw: str | None = await asyncio.to_thread(r.get, key)
        if raw is not None:
            return json.loads(raw)  # type: ignore[no-any-return]
    except Exception as redis_err:
        logger.warning("cache_read_failed", key=key, error=str(redis_err))

    result = await producer()

    try:
        sanitized = _sanitize_floats(result)
        serialized = json.dumps(sanitized)
        r2 = get_redis_client()
        await asyncio.to_thread(r2.setex, key, ttl, serialized)
    except Exception as cache_err:
        logger.warning("cache_write_failed", key=key, error=str(cache_err))

    return result


async def cached_json_stale_on_error(
    key: str,
    ttl_fresh: int,
    ttl_stale: int,
    producer: Callable[[], Awaitable[T]],
) -> T:
    """Read-through JSON cache with stale-on-error fallback.

    Serves a fresh value when present. On a cache miss, runs ``producer``:
    - on success, the value is written to both a short-lived fresh key and a
      long-lived ``{key}:stale`` key, then returned;
    - on failure (e.g. Hyperliquid returns 429 / times out), the last known
      stale value is served if available; otherwise the error propagates.

    This keeps user-facing endpoints showing the last good data during HL
    rate-limit storms instead of rendering an empty result.
    """
    stale_key = f"{key}:stale"

    # 1. Fresh hit
    try:
        r = get_redis_client()
        raw: str | None = await asyncio.to_thread(r.get, key)
        if raw is not None:
            return json.loads(raw)  # type: ignore[no-any-return]
    except Exception as redis_err:
        logger.warning("cache_read_failed", key=key, error=str(redis_err))

    # 2. Miss → produce
    try:
        result = await producer()
    except Exception as producer_err:
        # 3. Producer failed → serve stale if we have it
        try:
            r_stale = get_redis_client()
            stale_raw: str | None = await asyncio.to_thread(r_stale.get, stale_key)
        except Exception as redis_err:
            logger.warning(
                "cache_stale_read_failed", key=stale_key, error=str(redis_err)
            )
            stale_raw = None
        if stale_raw is not None:
            logger.warning("cache_served_stale", key=key, error=str(producer_err))
            return json.loads(stale_raw)  # type: ignore[no-any-return]
        raise

    # 4. Success → refresh both layers
    try:
        serialized = json.dumps(_sanitize_floats(result))
        r2 = get_redis_client()
        await asyncio.to_thread(r2.setex, key, ttl_fresh, serialized)
        await asyncio.to_thread(r2.setex, stale_key, ttl_stale, serialized)
    except Exception as cache_err:
        logger.warning("cache_write_failed", key=key, error=str(cache_err))

    return result


async def cache_delete(key: str) -> None:
    r = get_redis_client()
    await asyncio.to_thread(r.delete, key)


async def cache_get_raw(key: str) -> str | None:
    r = get_redis_client()
    return await asyncio.to_thread(r.get, key)

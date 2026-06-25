"""Process-wide weight-aware rate limiter for the Hyperliquid Info API.

Hyperliquid throttles the info endpoint by a per-IP *weight* budget (~1200
weight/minute). Cheap calls like ``clearinghouseState`` cost ~2 weight while
heavy history calls like ``userFills`` cost ~20. With a single uvicorn process
serving real-time polling, user requests, and background analytics, bursts can
exceed the budget and trigger HTTP 429 — which surfaces to users as failed
requests (e.g. an empty "Trades" tab).

This token bucket smooths all HL calls under a safe sustained rate. It also
supports a low-priority mode: background analytics yields a reserve of tokens so
latency-sensitive polling and user requests are never starved.
"""

import asyncio
import time
from contextvars import ContextVar

# When True, the current task's HL calls are treated as background (analytics)
# and must leave ``_LOW_PRIO_RESERVE`` weight available for higher-priority work.
hl_priority_low: ContextVar[bool] = ContextVar("hl_priority_low", default=False)

# Budget tuned to stay under HL's ~1200 weight/min with headroom.
_RATE_PER_SEC = 18.0       # sustained weight/second (~1080/min)
_CAPACITY = 40.0           # max burst weight
_LOW_PRIO_RESERVE = 20.0   # weight kept free for high-priority calls

# Per-request weights by info "type". Unlisted types default to the heavy cost.
_LIGHT_TYPES = frozenset(
    {
        "clearinghouseState",
        "allMids",
        "l2Book",
        "meta",
        "metaAndAssetCtxs",
        "orderStatus",
        "spotClearinghouseState",
    }
)
_HEAVY_WEIGHT = 20.0
_LIGHT_WEIGHT = 2.0


def weight_for_payload(payload: dict[str, object]) -> float:
    """Return the rate-limit weight for an info request payload."""
    return _LIGHT_WEIGHT if payload.get("type") in _LIGHT_TYPES else _HEAVY_WEIGHT


class HLRateLimiter:
    """Async token bucket with optional low-priority reserve."""

    def __init__(
        self, rate_per_sec: float, capacity: float, low_prio_reserve: float
    ) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity
        self._reserve = low_prio_reserve
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: float, low_priority: bool) -> None:
        """Block until ``weight`` tokens are available, then consume them.

        Low-priority callers additionally require ``_reserve`` tokens to remain,
        so they back off while high-priority traffic drains the bucket.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) * self._rate,
                )
                self._updated = now
                needed = weight + (self._reserve if low_priority else 0.0)
                if self._tokens >= needed:
                    self._tokens -= weight
                    return
                deficit = needed - self._tokens
            # Sleep outside the lock so other callers can re-check the bucket;
            # cap the wait so high-priority calls re-evaluate promptly.
            await asyncio.sleep(min(deficit / self._rate, 0.5))


# Shared singleton — all HyperliquidInfoClient instances throttle together.
hl_rate_limiter = HLRateLimiter(_RATE_PER_SEC, _CAPACITY, _LOW_PRIO_RESERVE)

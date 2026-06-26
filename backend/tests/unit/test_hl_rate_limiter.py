import asyncio
import time

from app.services.hyperliquid.rate_limiter import (
    HLRateLimiter,
    weight_for_payload,
)


class TestWeightForPayload:
    def test_light_type_is_cheap(self) -> None:
        assert weight_for_payload({"type": "clearinghouseState"}) == 2.0

    def test_heavy_type_is_expensive(self) -> None:
        assert weight_for_payload({"type": "userFills"}) == 20.0

    def test_unknown_type_defaults_to_heavy(self) -> None:
        assert weight_for_payload({"type": "somethingNew"}) == 20.0


class TestHLRateLimiter:
    async def test_burst_within_capacity_is_immediate(self) -> None:
        limiter = HLRateLimiter(rate_per_sec=10.0, capacity=40.0, low_prio_reserve=20.0)
        start = time.monotonic()
        for _ in range(4):
            await limiter.acquire(10.0, low_priority=False)
        assert time.monotonic() - start < 0.1

    async def test_exceeding_capacity_blocks_until_refill(self) -> None:
        limiter = HLRateLimiter(rate_per_sec=100.0, capacity=20.0, low_prio_reserve=0.0)
        # Drain the bucket, then a further request must wait for refill.
        await limiter.acquire(20.0, low_priority=False)
        start = time.monotonic()
        await limiter.acquire(10.0, low_priority=False)
        elapsed = time.monotonic() - start
        # Needs ~10 tokens at 100/s ≈ 0.1s.
        assert elapsed >= 0.05

    async def test_low_priority_yields_to_high_priority(self) -> None:
        # Slow refill so the bucket level is effectively static during the test.
        limiter = HLRateLimiter(rate_per_sec=1.0, capacity=30.0, low_prio_reserve=20.0)
        # Drain to ~20 tokens: each low-priority call needs weight+reserve = 25.
        await limiter.acquire(5.0, low_priority=True)  # 30 -> 25
        await limiter.acquire(5.0, low_priority=True)  # 25 -> 20

        # With ~20 tokens left, a further low-priority call needs 25 → blocks,
        # but a high-priority call needs only its weight (5) → proceeds at once.
        high_done = asyncio.Event()

        async def high() -> None:
            await limiter.acquire(5.0, low_priority=False)
            high_done.set()

        async def low() -> None:
            await limiter.acquire(5.0, low_priority=True)

        low_task = asyncio.create_task(low())
        high_task = asyncio.create_task(high())

        await asyncio.wait_for(high_done.wait(), timeout=1.0)
        assert high_task.done()
        assert not low_task.done()

        low_task.cancel()

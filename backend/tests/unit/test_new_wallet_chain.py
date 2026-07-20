from datetime import datetime
from decimal import Decimal

import pytest

from app.services.hyperliquid.funding_events import FundingEvent, FundingEventProvider
from app.services.hyperliquid.models import AccountEquitySnapshot
from app.services.new_wallets.chain import build_funding_chain


class FakeProvider(FundingEventProvider):
    def __init__(self, events: dict[str, FundingEvent]) -> None:
        self.events = events

    async def fetch_events_since(self, *, start_time, cursor, limit):
        raise AssertionError("global fetch is not used by chain tests")

    async def latest_incoming_for_address(self, address: str, *, before_time=None):
        return self.events.get(address)


class FakeClient:
    def __init__(
        self,
        balances: dict[str, Decimal],
        *,
        fills: dict[str, list[object]] | None = None,
        positions: dict[str, list[object]] | None = None,
    ) -> None:
        self.balances = balances
        self.fills = fills or {}
        self.positions = positions or {}

    async def get_fills_by_time(self, address: str, **kwargs):
        return self.fills.get(address, [])

    async def get_positions(self, address: str):
        return self.positions.get(address, [])

    async def get_account_equity_usd(self, address: str) -> AccountEquitySnapshot:
        value = self.balances[address]
        return AccountEquitySnapshot(
            balance_usd=value,
            balance_source="test",
            perp_account_value_usd=value,
            spot_usdc_total=Decimal("0"),
            evidence={"balance_source": "test"},
        )


def event(target: str, source: str | None, amount: str = "100") -> FundingEvent:
    return FundingEvent(
        targetAddress=target,
        sourceAddress=source,
        amountUsdc=Decimal(amount),
        txHash=f"0x{target[-4:]}",
        eventTime=datetime(2026, 7, 20, 12, 0, 0),
        eventType="deposit",
        rawEvent={"target": target, "source": source},
    )


TARGET = "0x" + "aa" * 20
W1 = "0x" + "11" * 20
W2 = "0x" + "22" * 20
W3 = "0x" + "33" * 20


@pytest.mark.asyncio
async def test_one_step_pass() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider({TARGET: event(TARGET, W1)}),
        client=FakeClient({W1: Decimal("16000")}),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.qualified is True
    assert result.chain_depth == 1


@pytest.mark.asyncio
async def test_three_step_pass_by_cumulative_balance() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider(
            {
                TARGET: event(TARGET, W1),
                W1: event(W1, W2),
                W2: event(W2, W3),
            }
        ),
        client=FakeClient(
            {W1: Decimal("5000"), W2: Decimal("6000"), W3: Decimal("5000")}
        ),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.qualified is True
    assert result.chain_depth == 3
    assert float(result.chain_total_balance_usd) == pytest.approx(16000)


@pytest.mark.asyncio
async def test_three_step_fail_below_threshold() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider(
            {
                TARGET: event(TARGET, W1),
                W1: event(W1, W2),
                W2: event(W2, W3),
            }
        ),
        client=FakeClient(
            {W1: Decimal("1000"), W2: Decimal("1000"), W3: Decimal("1000")}
        ),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.qualified is False
    assert result.reject_reason == "insufficient_chain_balance"


@pytest.mark.asyncio
async def test_missing_source_fails() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider({TARGET: event(TARGET, None)}),
        client=FakeClient({}),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.reject_reason == "missing_funding_source"


@pytest.mark.asyncio
async def test_cycle_detection() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider({TARGET: event(TARGET, W1), W1: event(W1, TARGET)}),
        client=FakeClient({W1: Decimal("1")}),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.reject_reason == "chain_cycle"


@pytest.mark.asyncio
async def test_already_trading_rejected() -> None:
    result = await build_funding_chain(
        TARGET,
        provider=FakeProvider({TARGET: event(TARGET, W1)}),
        client=FakeClient({W1: Decimal("16000")}, fills={TARGET: [object()]}),
        threshold_usd=Decimal("15000"),
        max_depth=3,
    )

    assert result.reject_reason == "already_trading"

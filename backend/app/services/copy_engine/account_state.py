import asyncio
from dataclasses import dataclass
from decimal import Decimal

from app.services.copy_engine.market_registry import RegistrySnapshot
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import OpenOrder, Position

SUPPORTED_ACCOUNT_MODES = frozenset({"standard", "unified"})


@dataclass(frozen=True)
class DexAccountState:
    dex: str
    positions: tuple[Position, ...]
    open_orders: tuple[OpenOrder, ...]
    account_value_usd: Decimal | None
    margin_used_usd: Decimal | None


@dataclass(frozen=True)
class CopyAccountSnapshot:
    account_address: str
    mode: str
    equity_usd: Decimal | None
    dex_states: tuple[DexAccountState, ...]

    @property
    def positions(self) -> tuple[tuple[str, Position], ...]:
        return tuple(
            (state.dex, position)
            for state in self.dex_states
            for position in state.positions
        )

    @property
    def open_orders(self) -> tuple[tuple[str, OpenOrder], ...]:
        return tuple(
            (state.dex, order)
            for state in self.dex_states
            for order in state.open_orders
        )


class AccountStateReader:
    def __init__(self, client: HyperliquidInfoClient | None = None) -> None:
        self._client = client or HyperliquidInfoClient()

    async def read(
        self,
        account_address: str,
        registry: RegistrySnapshot,
    ) -> CopyAccountSnapshot:
        abstraction = await self._client.get_user_abstraction(account_address)
        mode = abstraction.mode
        dexes = tuple(dict.fromkeys(market.dex for market in registry.markets))

        async def read_dex(dex: str) -> DexAccountState:
            state, orders = await asyncio.gather(
                self._client.get_clearinghouse_state(account_address, dex),
                self._client.get_open_orders(account_address, dex),
            )
            summary = state.margin_summary
            return DexAccountState(
                dex=dex,
                positions=tuple(state.open_positions),
                open_orders=tuple(orders),
                account_value_usd=(summary.account_value if summary else None),
                margin_used_usd=(summary.total_margin_used if summary else None),
            )

        dex_states = tuple(await asyncio.gather(*(read_dex(dex) for dex in dexes)))
        equity: Decimal | None
        if mode == "standard":
            values = [
                state.account_value_usd
                for state in dex_states
                if state.account_value_usd is not None
            ]
            equity = sum(values, start=Decimal("0")) if values else None
        elif mode == "unified":
            balances = await self._client.get_spot_balances(account_address)
            usdc = [balance.total for balance in balances if balance.coin == "USDC"]
            # Unified default marginSummary can include collateral already present
            # in spot and must not be counted again. USDC spot total is the
            # conservative collateral value until token-oracle valuation is added.
            equity = sum(usdc, start=Decimal("0")) if usdc else None
        else:
            equity = None

        return CopyAccountSnapshot(
            account_address=account_address,
            mode=mode,
            equity_usd=equity,
            dex_states=dex_states,
        )

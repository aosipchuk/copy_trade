"""Read-only-by-default Copy Engine v2 Hyperliquid testnet smoke."""

import argparse
import asyncio
import os
import sys

from app.core.config import settings
from app.services.copy_engine.account_state import AccountStateReader
from app.services.copy_engine.market_registry import MarketRegistry
from app.services.hyperliquid.info_client import HyperliquidInfoClient


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ack-testnet", action="store_true")
    return parser.parse_args()


async def _run() -> int:
    args = _arguments()
    if settings.hl_network != "testnet":
        print("Refusing to run: HL_NETWORK must be testnet.", file=sys.stderr)
        return 2
    if not args.ack_testnet:
        print("Refusing to run without --ack-testnet.", file=sys.stderr)
        return 2
    master = os.environ.get("HL_TEST_MASTER_ADDRESS", "").strip().lower()
    account = os.environ.get("HL_TEST_COPY_ACCOUNT_ADDRESS", master).strip().lower()
    if not master or not account:
        print(
            "HL_TEST_MASTER_ADDRESS and HL_TEST_COPY_ACCOUNT_ADDRESS are required.",
            file=sys.stderr,
        )
        return 2

    client = HyperliquidInfoClient()
    options = {master}
    options.update(
        subaccount.sub_account_user.lower()
        for subaccount in await client.get_subaccounts(master)
    )
    if account not in options:
        print("Copy account is not owned by the test master.", file=sys.stderr)
        return 2

    registry = await MarketRegistry(client).refresh()
    snapshot = await AccountStateReader(client).read(account, registry)
    if snapshot.mode not in {"standard", "unified"}:
        print(f"Unsupported test account mode: {snapshot.mode}", file=sys.stderr)
        return 1
    if snapshot.positions or snapshot.open_orders:
        print("Test copy account must be flat with no open orders.", file=sys.stderr)
        return 1
    hip3 = [market for market in registry.markets.values() if market.dex]
    if not hip3:
        print("No HIP-3 market metadata was discovered.", file=sys.stderr)
        return 1
    print(
        "Testnet smoke passed: ownership, account mode, flat state, default DEX "
        f"and {len(hip3)} HIP-3 markets validated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))

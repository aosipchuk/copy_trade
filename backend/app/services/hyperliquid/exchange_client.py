"""
Hyperliquid Exchange API client with EIP-712 action signing.

IMPORTANT: The L1 action signing logic (_compute_connection_id, _sign_l1_action)
MUST be validated against Hyperliquid testnet before mainnet use. See plans/MVP_PLAN.md.
"""

import struct
import time
from decimal import Decimal
from typing import Any

import httpx
import msgpack
from eth_account import Account
from eth_utils import keccak  # type: ignore[attr-defined]
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.services.hyperliquid.models import PlacementResult

logger = get_logger(__name__)

_ARBITRUM_CHAIN_ID = 42161  # used for user-facing approveAgent EIP-712
_TESTNET_CHAIN_ID = 421614  # used for user-facing approveAgent EIP-712
_L1_CHAIN_ID = 1337  # fixed domain for all agent-signed L1 actions (orders, etc.)
_EXCHANGE_TIMEOUT = httpx.Timeout(30.0)


def _compute_connection_id(
    action: dict[str, Any], nonce: int, vault_address: str | None = None
) -> bytes:
    """
    Compute keccak256(msgpack(action) + nonce_be64 + vault_flag[+ vault_bytes]).
    Matches the Hyperliquid Python SDK signing.action_hash implementation.
    """
    data = msgpack.packb(action, use_bin_type=True)
    data += struct.pack(">Q", nonce)
    if vault_address is None:
        data += b"\x00"
    else:
        addr_bytes = bytes.fromhex(vault_address.lower().removeprefix("0x").zfill(40))
        data += b"\x01" + addr_bytes
    return keccak(primitive=data)


def _sign_l1_action(
    agent_key: str,
    action: dict[str, Any],
    nonce: int,
    is_mainnet: bool,
    vault_address: str | None = None,
) -> dict[str, str | int]:
    """Sign a Hyperliquid L1 action using EIP-712 Agent type."""
    connection_id = _compute_connection_id(action, nonce, vault_address)
    full_message: dict[str, Any] = {
        "domain": {
            "chainId": _L1_CHAIN_ID,
            "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ]
        },
        "primaryType": "Agent",
        "message": {
            "source": "a" if is_mainnet else "b",
            "connectionId": connection_id,
        },
    }
    signed = Account.sign_typed_data(private_key=agent_key, full_message=full_message)
    return {
        "r": "0x" + signed.r.to_bytes(32, "big").hex(),
        "s": "0x" + signed.s.to_bytes(32, "big").hex(),
        "v": signed.v,
    }


class HyperliquidExchangeClient:
    """Async client for Hyperliquid exchange API (orders, agent approval)."""

    def __init__(self) -> None:
        self._exchange_url = f"{settings.hl_api_url}/exchange"
        self._is_mainnet = settings.hl_network == "mainnet"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT) as client:
            resp = await client.post(self._exchange_url, json=payload)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def place_order(
        self,
        agent_key: str,
        coin: str,
        asset_index: int,
        is_buy: bool,
        size: Decimal,
        limit_px: Decimal,
        cloid: str,
        reduce_only: bool = False,
        include_builder: bool = False,
        vault_address: str | None = None,
    ) -> PlacementResult:
        """
        Place an IOC limit order on behalf of the agent.
        The caller persists ``cloid`` before this call. A transport timeout is
        deliberately raised so the reconciler can recover by cloid.
        """
        normalized_cloid = cloid.lower()
        if (
            len(normalized_cloid) != 34
            or not normalized_cloid.startswith("0x")
        ):
            raise ValueError("cloid must be a 128-bit 0x-prefixed hex string")
        int(normalized_cloid[2:], 16)
        nonce = int(time.time() * 1000)
        action: dict[str, Any] = {
            "type": "order",
            "orders": [
                {
                    "a": asset_index,
                    "b": is_buy,
                    "p": str(limit_px),
                    "s": str(size),
                    "r": reduce_only,
                    "t": {"limit": {"tif": "Ioc"}},
                    "c": normalized_cloid,
                }
            ],
            "grouping": "na",
        }
        if (
            include_builder
            and settings.builder_address
            and settings.builder_fee_rate > 0
        ):  # noqa: E501
            action["builder"] = {
                "b": settings.builder_address.lower(),
                "f": settings.builder_fee_rate,
            }
        signature = _sign_l1_action(
            agent_key, action, nonce, self._is_mainnet, vault_address
        )
        payload: dict[str, Any] = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
        }
        if vault_address is not None:
            payload["vaultAddress"] = vault_address.lower()
        result = await self._post(payload)

        if result.get("status") != "ok":
            logger.warning("hl_order_rejected", coin=coin, response=result)
            return PlacementResult(status="failed", error=str(result))

        statuses: list[Any] = (
            result.get("response", {}).get("data", {}).get("statuses", [])
        )  # noqa: E501
        if not statuses:
            return PlacementResult(status="unknown", error="Empty order status")

        first = statuses[0]
        if "resting" in first:
            return PlacementResult(status="submitted", oid=int(first["resting"]["oid"]))
        if "filled" in first:
            filled = first["filled"]
            return PlacementResult(
                status="filled",
                oid=int(filled["oid"]),
                filled_size=Decimal(str(filled.get("totalSz", size))),
                average_price=Decimal(str(filled.get("avgPx", limit_px))),
            )
        if "error" in first:
            logger.warning("hl_order_error", coin=coin, error=first["error"])
            return PlacementResult(status="failed", error=str(first["error"]))
        return PlacementResult(status="unknown", error="Unrecognized order response")

    async def close_position(
        self,
        agent_key: str,
        coin: str,
        asset_index: int,
        is_long: bool,
        size: Decimal,
        limit_px: Decimal,
        cloid: str,
        include_builder: bool = False,
        vault_address: str | None = None,
    ) -> PlacementResult:
        """Close an existing position using a reduce-only IOC order."""
        return await self.place_order(
            agent_key=agent_key,
            coin=coin,
            asset_index=asset_index,
            is_buy=not is_long,
            size=size,
            limit_px=limit_px,
            cloid=cloid,
            reduce_only=True,
            include_builder=include_builder,
            vault_address=vault_address,
        )

    async def get_order_status(self, owner_address: str, order_id: int | str) -> str:
        """
        Return order status: 'open' | 'filled' | 'cancelled' | 'unknown'.
        Uses Info API since exchange API has no dedicated status endpoint.
        """
        from app.services.hyperliquid.info_client import HyperliquidInfoClient

        return (await HyperliquidInfoClient().get_order_status(
            owner_address,
            order_id,
        )).status

    async def cancel_by_cloid(
        self,
        *,
        agent_key: str,
        asset_index: int,
        cloid: str,
        vault_address: str | None = None,
    ) -> PlacementResult:
        """Explicit recovery primitive; callers must query status before cancel."""
        nonce = int(time.time() * 1000)
        action: dict[str, Any] = {
            "type": "cancelByCloid",
            "cancels": [{"asset": asset_index, "cloid": cloid.lower()}],
        }
        signature = _sign_l1_action(
            agent_key,
            action,
            nonce,
            self._is_mainnet,
            vault_address,
        )
        payload: dict[str, Any] = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
        }
        if vault_address is not None:
            payload["vaultAddress"] = vault_address.lower()
        result = await self._post(payload)
        if result.get("status") != "ok":
            return PlacementResult(status="failed", error=str(result))
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        first = statuses[0] if statuses else None
        if first == "success":
            return PlacementResult(status="cancelled")
        return PlacementResult(status="failed", error=str(first or result))

    def build_approve_agent_payload(
        self, agent_address: str, nonce: int
    ) -> dict[str, Any]:
        """Return the EIP-712 typed data the user's wallet must sign for approveAgent."""  # noqa: E501
        chain_id = _ARBITRUM_CHAIN_ID if self._is_mainnet else _TESTNET_CHAIN_ID
        hl_chain = "Mainnet" if self._is_mainnet else "Testnet"
        return {
            "domain": {
                "chainId": chain_id,
                "name": "Exchange",
                "verifyingContract": "0x0000000000000000000000000000000000000000",
                "version": "1",
            },
            "types": {
                "HyperliquidTransaction:ApproveAgent": [
                    {"name": "hyperliquidChain", "type": "string"},
                    {"name": "agentAddress", "type": "address"},
                    {"name": "agentName", "type": "string"},
                    {"name": "nonce", "type": "uint64"},
                ]
            },
            "primaryType": "HyperliquidTransaction:ApproveAgent",
            "message": {
                "hyperliquidChain": hl_chain,
                "agentAddress": agent_address,
                "agentName": "copy-trade",
                "nonce": nonce,
            },
        }

    def build_approve_builder_fee_payload(self, nonce: int) -> dict[str, Any]:
        """Return EIP-712 typed data for ApproveBuilderFee user signature."""
        chain_id = _ARBITRUM_CHAIN_ID if self._is_mainnet else _TESTNET_CHAIN_ID
        hl_chain = "Mainnet" if self._is_mainnet else "Testnet"
        return {
            "domain": {
                "chainId": chain_id,
                "name": "Exchange",
                "verifyingContract": "0x0000000000000000000000000000000000000000",
                "version": "1",
            },
            "types": {
                "HyperliquidTransaction:ApproveBuilderFee": [
                    {"name": "hyperliquidChain", "type": "string"},
                    {"name": "maxFeeRate", "type": "string"},
                    {"name": "builder", "type": "address"},
                    {"name": "nonce", "type": "uint64"},
                ]
            },
            "primaryType": "HyperliquidTransaction:ApproveBuilderFee",
            "message": {
                "hyperliquidChain": hl_chain,
                "maxFeeRate": settings.builder_max_fee_rate,
                "builder": settings.builder_address,
                "nonce": nonce,
            },
        }

    async def submit_approve_builder_fee(
        self,
        nonce: int,
        signature: dict[str, Any],
    ) -> str | None:
        """Submit approveBuilderFee to HL. Returns None on success, error string on failure."""  # noqa: E501
        hl_chain = "Mainnet" if self._is_mainnet else "Testnet"
        sig_chain_id = "0xa4b1" if self._is_mainnet else "0x66eee"
        action: dict[str, Any] = {
            "type": "approveBuilderFee",
            "hyperliquidChain": hl_chain,
            "signatureChainId": sig_chain_id,
            "maxFeeRate": settings.builder_max_fee_rate,
            "builder": settings.builder_address,
            "nonce": nonce,
        }
        result = await self._post(
            {"action": action, "nonce": nonce, "signature": signature}
        )
        if result.get("status") == "ok":
            return None
        error_msg = result.get("response", "Unknown error from Hyperliquid")
        logger.warning("hl_approve_builder_fee_failed", response=result)
        return str(error_msg)

    async def submit_approve_agent(
        self,
        agent_address: str,
        nonce: int,
        signature: dict[str, Any],
    ) -> str | None:
        """
        Submit approveAgent to HL exchange.
        Returns None on success, or the error string from HL on failure.
        """
        hl_chain = "Mainnet" if self._is_mainnet else "Testnet"
        sig_chain_id = "0xa4b1" if self._is_mainnet else "0x66eee"
        action: dict[str, Any] = {
            "type": "approveAgent",
            "hyperliquidChain": hl_chain,
            "signatureChainId": sig_chain_id,
            "agentAddress": agent_address,
            "agentName": "copy-trade",
            "nonce": nonce,
        }
        result = await self._post(
            {"action": action, "nonce": nonce, "signature": signature}
        )  # noqa: E501
        if result.get("status") == "ok":
            return None
        error_msg = result.get("response", "Unknown error from Hyperliquid")
        logger.warning("hl_approve_agent_failed", agent=agent_address, response=result)
        return str(error_msg)

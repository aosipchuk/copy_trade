"""
Unit tests for Hyperliquid EIP-712 signing.

Validates _compute_connection_id and _sign_l1_action against a reference
implementation derived from the official Hyperliquid Python SDK
(github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/utils/signing.py).
"""

from typing import Any

import msgpack
from eth_utils import keccak

from app.services.hyperliquid.exchange_client import (
    _L1_CHAIN_ID,
    _compute_connection_id,
    _sign_l1_action,
)

# ---------------------------------------------------------------------------
# Reference implementation (SDK-faithful, used only in tests)
# ---------------------------------------------------------------------------

_TEST_ACTION: dict[str, Any] = {
    "type": "order",
    "orders": [
        {
            "a": 0,
            "b": True,
            "p": "50000.0",
            "s": "0.01",
            "r": False,
            "t": {"limit": {"tif": "Ioc"}},
        }
    ],
    "grouping": "na",
}
_TEST_NONCE = 1_700_000_000_123
_TEST_VAULT = "0xabcdef1234567890abcdef1234567890abcdef12"
_TEST_PRIVATE_KEY = "0x" + "aa" * 32


def _ref_action_hash(
    action: dict[str, Any],
    vault_address: str | None,
    nonce: int,
) -> bytes:
    """SDK reference: hyperliquid/utils/signing.py::action_hash."""
    data = msgpack.packb(action)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        addr = vault_address.removeprefix("0x")
        data += b"\x01" + bytes.fromhex(addr)
    return keccak(primitive=data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeConnectionId:
    def test_matches_reference_no_vault(self) -> None:
        ours = _compute_connection_id(_TEST_ACTION, _TEST_NONCE, None)
        ref = _ref_action_hash(_TEST_ACTION, None, _TEST_NONCE)
        assert ours == ref

    def test_matches_reference_with_vault(self) -> None:
        ours = _compute_connection_id(_TEST_ACTION, _TEST_NONCE, _TEST_VAULT)
        ref = _ref_action_hash(_TEST_ACTION, _TEST_VAULT, _TEST_NONCE)
        assert ours == ref

    def test_different_nonces_produce_different_hashes(self) -> None:
        h1 = _compute_connection_id(_TEST_ACTION, 1000, None)
        h2 = _compute_connection_id(_TEST_ACTION, 1001, None)
        assert h1 != h2

    def test_different_actions_produce_different_hashes(self) -> None:
        action2 = {**_TEST_ACTION, "grouping": "normalTpsl"}
        h1 = _compute_connection_id(_TEST_ACTION, _TEST_NONCE, None)
        h2 = _compute_connection_id(action2, _TEST_NONCE, None)
        assert h1 != h2

    def test_returns_bytes32(self) -> None:
        result = _compute_connection_id(_TEST_ACTION, _TEST_NONCE, None)
        assert isinstance(result, bytes)
        assert len(result) == 32


class TestSignL1Action:
    def test_uses_l1_chain_id_in_domain(self) -> None:
        """The EIP-712 domain for agent L1 actions always uses chainId=1337."""
        assert _L1_CHAIN_ID == 1337

    def test_signature_fields_present(self) -> None:
        sig = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        assert set(sig.keys()) == {"r", "s", "v"}

    def test_signature_hex_format(self) -> None:
        sig = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        assert isinstance(sig["r"], str) and sig["r"].startswith("0x")
        assert isinstance(sig["s"], str) and sig["s"].startswith("0x")
        assert sig["v"] in (27, 28)

    def test_r_s_are_32_bytes(self) -> None:
        """r and s must be zero-padded 32-byte hex to avoid HL API rejection."""
        sig = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        assert len(sig["r"]) == 66  # 0x + 64 hex chars
        assert len(sig["s"]) == 66

    def test_mainnet_testnet_produce_different_signatures(self) -> None:
        """source field differs: 'a' (mainnet) vs 'b' (testnet) → different sig."""
        sig_main = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        sig_test = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=False
        )
        assert sig_main["r"] != sig_test["r"]

    def test_signing_is_deterministic(self) -> None:
        """Same inputs must always produce the same signature (RFC 6979)."""
        sig1 = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        sig2 = _sign_l1_action(
            _TEST_PRIVATE_KEY, _TEST_ACTION, _TEST_NONCE, is_mainnet=True
        )
        assert sig1["r"] == sig2["r"]
        assert sig1["s"] == sig2["s"]
        assert sig1["v"] == sig2["v"]


class TestApproveAgentPayload:
    """Tests use _is_mainnet=False directly to avoid settings lru_cache."""

    def _testnet_client(self) -> Any:
        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        client = HyperliquidExchangeClient()
        client._is_mainnet = False
        return client

    def _mainnet_client(self) -> Any:
        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        client = HyperliquidExchangeClient()
        client._is_mainnet = True
        return client

    def test_primary_type_is_hyperliquid_transaction(self) -> None:
        payload = self._testnet_client().build_approve_agent_payload(
            agent_address="0x1234567890abcdef1234567890abcdef12345678",
            nonce=1_700_000_000_000,
        )
        assert payload["primaryType"] == "HyperliquidTransaction:ApproveAgent"

    def test_types_key_matches_primary_type(self) -> None:
        payload = self._testnet_client().build_approve_agent_payload(
            agent_address="0x1234567890abcdef1234567890abcdef12345678",
            nonce=1_700_000_000_000,
        )
        assert "HyperliquidTransaction:ApproveAgent" in payload["types"]
        assert "ApproveAgent" not in payload["types"]

    def test_testnet_uses_arbitrum_sepolia_chain_id(self) -> None:
        payload = self._testnet_client().build_approve_agent_payload(
            agent_address="0x1234567890abcdef1234567890abcdef12345678",
            nonce=1_700_000_000_000,
        )
        assert payload["domain"]["chainId"] == 421614  # Arbitrum Sepolia

    def test_mainnet_uses_arbitrum_one_chain_id(self) -> None:
        payload = self._mainnet_client().build_approve_agent_payload(
            agent_address="0x1234567890abcdef1234567890abcdef12345678",
            nonce=1_700_000_000_000,
        )
        assert payload["domain"]["chainId"] == 42161  # Arbitrum One

    def test_testnet_message_fields_present(self) -> None:
        payload = self._testnet_client().build_approve_agent_payload(
            agent_address="0x1234567890abcdef1234567890abcdef12345678",
            nonce=1_700_000_000_000,
        )
        msg = payload["message"]
        assert msg["hyperliquidChain"] == "Testnet"
        assert msg["agentName"] == "copy-trade"
        assert msg["nonce"] == 1_700_000_000_000


class TestApproveBuilderFeePayload:
    """Tests for build_approve_builder_fee_payload."""

    def _testnet_client(self) -> Any:
        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        client = HyperliquidExchangeClient()
        client._is_mainnet = False
        return client

    def _mainnet_client(self) -> Any:
        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        client = HyperliquidExchangeClient()
        client._is_mainnet = True
        return client

    def test_primary_type_is_approve_builder_fee(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["primaryType"] == "HyperliquidTransaction:ApproveBuilderFee"

    def test_types_key_matches_primary_type(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert "HyperliquidTransaction:ApproveBuilderFee" in payload["types"]

    def test_types_fields_are_correct(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        fields = {
            f["name"]: f["type"]
            for f in payload["types"]["HyperliquidTransaction:ApproveBuilderFee"]
        }
        assert fields["hyperliquidChain"] == "string"
        assert fields["maxFeeRate"] == "string"
        assert fields["builder"] == "address"
        assert fields["nonce"] == "uint64"

    def test_testnet_uses_arbitrum_sepolia_chain_id(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["domain"]["chainId"] == 421614

    def test_mainnet_uses_arbitrum_one_chain_id(self) -> None:
        payload = self._mainnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["domain"]["chainId"] == 42161

    def test_testnet_message_chain_is_testnet(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["message"]["hyperliquidChain"] == "Testnet"

    def test_mainnet_message_chain_is_mainnet(self) -> None:
        payload = self._mainnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["message"]["hyperliquidChain"] == "Mainnet"

    def test_nonce_present_in_message(self) -> None:
        payload = self._testnet_client().build_approve_builder_fee_payload(
            nonce=1_700_000_000_000
        )
        assert payload["message"]["nonce"] == 1_700_000_000_000


class TestPlaceOrderBuilderField:
    """Tests that include_builder correctly injects builder into action dict."""

    _TEST_AGENT_KEY = "0x" + "aa" * 32
    _COIN = "BTC"
    _ASSET_IDX = 0

    def test_action_contains_builder_when_include_builder_true(self) -> None:
        from unittest.mock import patch

        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        captured: list[dict[str, Any]] = []

        async def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
            captured.append(payload)
            return {"status": "ok", "response": {"data": {"statuses": []}}}

        client = HyperliquidExchangeClient()
        client._is_mainnet = False

        with (
            patch.object(client, "_post", side_effect=fake_post),
            patch("app.services.hyperliquid.exchange_client.settings") as mock_settings,
        ):
            mock_settings.builder_address = "0xbuilder000000000000000000000000000000"
            mock_settings.builder_fee_rate = 50
            mock_settings.hl_network = "testnet"

            import asyncio
            from decimal import Decimal

            asyncio.run(
                client.place_order(
                    agent_key=self._TEST_AGENT_KEY,
                    coin=self._COIN,
                    asset_index=self._ASSET_IDX,
                    is_buy=True,
                    size=Decimal("0.01"),
                    limit_px=Decimal("50000"),
                    include_builder=True,
                )
            )

        assert len(captured) == 1
        action = captured[0]["action"]
        assert "builder" in action
        assert action["builder"]["f"] == 50

    def test_action_excludes_builder_when_include_builder_false(self) -> None:
        from unittest.mock import patch

        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        captured: list[dict[str, Any]] = []

        async def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
            captured.append(payload)
            return {"status": "ok", "response": {"data": {"statuses": []}}}

        client = HyperliquidExchangeClient()
        client._is_mainnet = False

        with patch.object(client, "_post", side_effect=fake_post):
            import asyncio
            from decimal import Decimal

            asyncio.run(
                client.place_order(
                    agent_key=self._TEST_AGENT_KEY,
                    coin=self._COIN,
                    asset_index=self._ASSET_IDX,
                    is_buy=True,
                    size=Decimal("0.01"),
                    limit_px=Decimal("50000"),
                    include_builder=False,
                )
            )

        assert len(captured) == 1
        action = captured[0]["action"]
        assert "builder" not in action

    def test_action_excludes_builder_when_builder_address_empty(self) -> None:
        from unittest.mock import patch

        from app.services.hyperliquid.exchange_client import HyperliquidExchangeClient

        captured: list[dict[str, Any]] = []

        async def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
            captured.append(payload)
            return {"status": "ok", "response": {"data": {"statuses": []}}}

        client = HyperliquidExchangeClient()
        client._is_mainnet = False

        with (
            patch.object(client, "_post", side_effect=fake_post),
            patch("app.services.hyperliquid.exchange_client.settings") as mock_settings,
        ):
            mock_settings.builder_address = ""
            mock_settings.builder_fee_rate = 50

            import asyncio
            from decimal import Decimal

            asyncio.run(
                client.place_order(
                    agent_key=self._TEST_AGENT_KEY,
                    coin=self._COIN,
                    asset_index=self._ASSET_IDX,
                    is_buy=True,
                    size=Decimal("0.01"),
                    limit_px=Decimal("50000"),
                    include_builder=True,
                )
            )

        assert len(captured) == 1
        action = captured[0]["action"]
        assert "builder" not in action

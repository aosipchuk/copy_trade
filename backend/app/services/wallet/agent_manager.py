import os
from typing import NamedTuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eth_account import Account

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_NONCE_SIZE = 12  # bytes for AES-GCM nonce


class AgentKeypair(NamedTuple):
    address: str
    private_key: str


def generate_agent_keypair() -> AgentKeypair:
    """Generate a fresh EVM keypair for use as a Hyperliquid agent."""
    account = Account.create()
    return AgentKeypair(address=account.address, private_key=account.key.hex())


def _aes_key() -> bytes:
    """Derive 32-byte AES key from the hex env var."""
    return bytes.fromhex(settings.agent_encryption_key)


def encrypt_agent_key(private_key: str) -> bytes:
    """Encrypt agent private key with AES-256-GCM. Returns nonce || ciphertext || tag."""  # noqa: E501
    key = _aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, private_key.encode(), None)
    return nonce + ciphertext


def decrypt_agent_key(blob: bytes) -> str:
    """Decrypt agent private key. Input must be nonce || ciphertext || tag."""
    key = _aes_key()
    aesgcm = AESGCM(key)
    nonce = blob[:_NONCE_SIZE]
    ciphertext = blob[_NONCE_SIZE:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode()

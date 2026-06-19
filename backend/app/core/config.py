from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_KEY_DEFAULT = "change-me-min-32-chars-random-string"  # noqa: S105
_AGENT_KEY_DEFAULT = "0" * 64


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = False
    secret_key: str = _SECRET_KEY_DEFAULT  # noqa: S105

    # PostgreSQL
    database_url: str = (
        "postgresql+asyncpg://copytrade:copytrade@localhost:5432/copytrade"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "copytrade"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Agent key encryption (32-byte hex)
    agent_encryption_key: str = _AGENT_KEY_DEFAULT

    # Hyperliquid
    hl_network: Literal["mainnet", "testnet"] = "mainnet"
    # Dev only: skip actual HL approveAgent call (no deposit required)
    hl_skip_approve: bool = False
    hl_testnet_api_url: str = "https://api.hyperliquid-testnet.xyz"
    hl_mainnet_api_url: str = "https://api.hyperliquid.xyz"
    hl_stats_url: str = "https://stats-data.hyperliquid.xyz/Mainnet"

    # Builder Code (monetization)
    builder_address: str = ""  # 0x... our wallet for receiving fees
    builder_fee_rate: int = 50  # tenth-bps: 50 = 0.05%, 100 = 0.1%
    builder_max_fee_rate: str = "0.075%"  # EIP-712 payload — slightly above real rate

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("secret_key")
    @classmethod
    def require_strong_secret_key(cls, v: str) -> str:
        if v == _SECRET_KEY_DEFAULT:
            raise ValueError(
                "SECRET_KEY must be changed from the placeholder default. "
                "Generate a strong random key (e.g. `openssl rand -hex 32`)."
            )
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long.")
        return v

    @field_validator("telegram_bot_token")
    @classmethod
    def require_telegram_bot_token(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN must be set. "
                "An empty token makes HMAC verification trivially bypassable."
            )
        return v

    @field_validator("agent_encryption_key")
    @classmethod
    def validate_agent_encryption_key(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError(
                "AGENT_ENCRYPTION_KEY must be exactly 64 hex characters (32 bytes)."
            )
        try:
            bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError(
                "AGENT_ENCRYPTION_KEY must be valid hexadecimal."
            ) from exc
        return v

    @model_validator(mode="after")
    def reject_weak_keys_in_production(self) -> "Settings":
        if self.environment == "production":
            if self.agent_encryption_key == _AGENT_KEY_DEFAULT:
                raise ValueError(
                    "AGENT_ENCRYPTION_KEY must not be the all-zeros placeholder in production. "
                    "All stored agent private keys would be trivially decryptable."
                )
        return self

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def hl_api_url(self) -> str:
        if self.hl_network == "testnet":
            return self.hl_testnet_api_url
        return self.hl_mainnet_api_url

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

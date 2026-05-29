from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = False
    secret_key: str = "change-me-min-32-chars-random-string"

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://copytrade:copytrade@localhost:5432/copytrade"

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
    agent_encryption_key: str = "0" * 64

    # Hyperliquid
    hl_network: Literal["mainnet", "testnet"] = "mainnet"
    hl_testnet_api_url: str = "https://api.hyperliquid-testnet.xyz"
    hl_mainnet_api_url: str = "https://api.hyperliquid.xyz"
    hl_stats_url: str = "https://stats-data.hyperliquid.xyz/Mainnet"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

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

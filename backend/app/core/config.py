from functools import lru_cache
from typing import Any, Literal

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
    public_url: str = ""

    # PostgreSQL
    database_url: str = (
        "postgresql+asyncpg://copytrade:copytrade@localhost:5432/copytrade"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    admin_telegram_ids: list[int] = []

    # Agent key encryption (32-byte hex)
    agent_encryption_key: str = _AGENT_KEY_DEFAULT

    # Hyperliquid
    hl_network: Literal["mainnet", "testnet"] = "mainnet"
    # Dev only: skip actual HL approveAgent call (no deposit required)
    hl_skip_approve: bool = False
    hl_testnet_api_url: str = "https://api.hyperliquid-testnet.xyz"
    hl_mainnet_api_url: str = "https://api.hyperliquid.xyz"
    hl_stats_url: str = "https://stats-data.hyperliquid.xyz/Mainnet"

    # Hyperliquid rate limiter (weight/sec budget; HL throttles ~1200 weight/min
    # per IP). Tunable via env without redeploy. Defaults leave headroom under
    # the limit; lower hl_rate_per_sec if 429s persist.
    hl_rate_per_sec: float = 18.0
    hl_rate_capacity: float = 80.0
    hl_rate_low_prio_reserve: float = 40.0
    # Cap how many active traders get quality metrics each cycle (heavy userFills
    # call each, ~20 weight). Traders are processed highest-30d-ROI first, so the
    # most-viewed get metrics; the rest keep NULL metrics until they rank higher.
    # This bounds the dominant background HL load that drives 429 storms.
    hl_quality_metrics_max_traders: int = 1000

    # Hydromancer (human-score filtering)
    hydromancer_api_key: str = ""
    hydromancer_api_url: str = "https://api.hydromancer.xyz"

    # Builder Code (monetization)
    builder_address: str = ""  # 0x... our wallet for receiving fees
    builder_fee_rate: int = 50  # tenth-bps: 50 = 0.05%, 100 = 0.1%
    builder_max_fee_rate: str = "0.075%"  # EIP-712 payload — slightly above real rate

    # Model portfolio billing
    billing_provider: Literal["stripe"] = "stripe"
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_portfolio_price_id: str = ""
    stripe_checkout_success_url: str = ""
    stripe_checkout_cancel_url: str = ""
    stripe_api_url: str = "https://api.stripe.com/v1"
    model_portfolio_beta_override_telegram_ids: list[int] = []

    # Model portfolio explanations. The default is deterministic templates.
    # openai_compatible is optional and falls back to templates on any error.
    model_portfolio_explanations_provider: Literal["template", "openai_compatible"] = (
        "template"
    )
    model_portfolio_llm_api_url: str = ""
    model_portfolio_llm_api_key: str = ""
    model_portfolio_llm_model: str = ""

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
            raise ValueError("AGENT_ENCRYPTION_KEY must be valid hexadecimal.") from exc
        return v

    @field_validator("model_portfolio_beta_override_telegram_ids", mode="before")
    @classmethod
    def parse_beta_override_telegram_ids(cls, v: Any) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(item.strip()) for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [int(item) for item in v]
        raise ValueError(
            "MODEL_PORTFOLIO_BETA_OVERRIDE_TELEGRAM_IDS must be a comma-separated "
            "list of Telegram IDs."
        )

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_telegram_ids(cls, v: Any) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(item.strip()) for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [int(item) for item in v]
        raise ValueError("ADMIN_TELEGRAM_IDS must be a comma-separated list.")

    @model_validator(mode="after")
    def reject_weak_keys_in_production(self) -> "Settings":
        if (
            self.environment == "production"
            and self.agent_encryption_key == _AGENT_KEY_DEFAULT
        ):
            raise ValueError(
                "AGENT_ENCRYPTION_KEY must not be the all-zeros placeholder "
                "in production. All stored agent private keys would be "
                "trivially decryptable."
            )
        return self

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def hl_api_url(self) -> str:
        if self.hl_network == "testnet":
            return self.hl_testnet_api_url
        return self.hl_mainnet_api_url

    @property
    def stripe_billing_configured(self) -> bool:
        return bool(
            self.stripe_api_key
            and self.stripe_portfolio_price_id
            and self.stripe_checkout_success_url
            and self.stripe_checkout_cancel_url
        )

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

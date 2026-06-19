from decimal import Decimal

# Top-20 coins by volume on Hyperliquid (MVP whitelist)
COIN_WHITELIST: frozenset[str] = frozenset(
    [
        "BTC",
        "ETH",
        "SOL",
        "ARB",
        "AVAX",
        "DOGE",
        "LINK",
        "BNB",
        "OP",
        "SUI",
        "INJ",
        "APT",
        "ATOM",
        "MATIC",
        "LTC",
        "NEAR",
        "FIL",
        "ADA",
        "XRP",
        "TON",
    ]
)

# Minimum trade size in USD
MIN_TRADE_USD: Decimal = Decimal("10")

# IOC order slippage tolerance (0.1%)
IOC_SLIPPAGE: Decimal = Decimal("0.001")

# Redis dedup window for signals (seconds)
DEDUP_TTL_SECONDS: int = 30

# Pending trade timeout before marking as failed (seconds)
PENDING_TRADE_TIMEOUT_SECONDS: int = 120

# Maximum number of active subscriptions per user
MAX_SUBSCRIPTIONS_PER_USER: int = 3

# Maximum allocation as fraction of equity
MAX_ALLOCATION_EQUITY_FRACTION: Decimal = Decimal("0.8")

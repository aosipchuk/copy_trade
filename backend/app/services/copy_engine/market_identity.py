from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class MarketId:
    dex: str
    coin: str

    @property
    def canonical_coin(self) -> str:
        if not self.dex:
            return self.coin
        prefix = f"{self.dex}:"
        return self.coin if self.coin.startswith(prefix) else f"{prefix}{self.coin}"

    @property
    def key(self) -> str:
        return f"{self.dex}|{self.canonical_coin}"


def market_id(dex: str | None, coin: str) -> MarketId:
    clean_dex = (dex or "").strip()
    clean_coin = coin.strip()
    if ":" in clean_coin:
        prefix, _ = clean_coin.split(":", 1)
        if clean_dex and prefix != clean_dex:
            raise ValueError("Coin prefix does not match DEX")
        clean_dex = clean_dex or prefix
    return MarketId(clean_dex, clean_coin)


def allowed_market(allowed_coins: list[str] | None, market: MarketId) -> bool:
    if allowed_coins is None:
        return True
    allowed = {item.strip() for item in allowed_coins}
    # An unprefixed legacy value only grants the default DEX.
    return market.canonical_coin in allowed and (
        not market.dex or ":" in market.canonical_coin
    )

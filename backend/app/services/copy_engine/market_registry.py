import asyncio
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.services.copy_engine.market_identity import MarketId, market_id
from app.services.hyperliquid.info_client import HyperliquidInfoClient

logger = get_logger(__name__)

_CACHE_KEY = "copy:v2:market_registry"


@dataclass(frozen=True)
class MarketSpec:
    dex: str
    coin: str
    asset_id: int
    sz_decimals: int
    max_leverage: int
    collateral_token: int | None
    mid_price: str | None
    is_active: bool
    is_delisted: bool
    is_halted: bool

    @property
    def market(self) -> MarketId:
        return market_id(self.dex, self.coin)

    @property
    def mid(self) -> Decimal | None:
        if self.mid_price is None:
            return None
        value = Decimal(self.mid_price)
        return value if value > 0 else None


@dataclass(frozen=True)
class RegistrySnapshot:
    loaded_at: float
    dex_names: tuple[str, ...]
    markets: dict[str, MarketSpec]

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.loaded_at)


class MarketRegistry:
    _snapshot: RegistrySnapshot | None = None
    _refresh_lock = asyncio.Lock()

    def __init__(self, client: HyperliquidInfoClient | None = None) -> None:
        self._client = client or HyperliquidInfoClient()

    async def get_snapshot(self, *, force_refresh: bool = False) -> RegistrySnapshot:
        snapshot = self.__class__._snapshot
        if not force_refresh and snapshot is not None and not self._stale(snapshot):
            return snapshot
        if not force_refresh:
            cached = self._read_cache()
            if cached is not None and not self._stale(cached):
                self.__class__._snapshot = cached
                return cached
        return await self.refresh()

    async def refresh(self) -> RegistrySnapshot:
        async with self.__class__._refresh_lock:
            dex_names = await self._client.get_perp_dexs()
            results = await asyncio.gather(
                *[self._load_dex(dex, index) for index, dex in enumerate(dex_names)],
                return_exceptions=True,
            )
            markets: dict[str, MarketSpec] = {}
            failed: list[str] = []
            for dex, result in zip(dex_names, results, strict=True):
                if isinstance(result, BaseException):
                    failed.append(dex or "default")
                    logger.warning(
                        "copy_market_registry_dex_failed", dex=dex, error=str(result)
                    )
                    continue
                for spec in result:
                    markets[spec.market.key] = spec
            if not markets or "default" in failed:
                raise RuntimeError("Default Hyperliquid market metadata unavailable")
            snapshot = RegistrySnapshot(
                loaded_at=time.time(),
                dex_names=tuple(dex_names),
                markets=markets,
            )
            self.__class__._snapshot = snapshot
            self._write_cache(snapshot)
            logger.info(
                "copy_market_registry_refreshed",
                markets=len(markets),
                dexs=len(dex_names),
                failed_dexs=failed,
            )
            return snapshot

    async def require_market(self, dex: str, coin: str) -> MarketSpec:
        snapshot = await self.get_snapshot()
        market = market_id(dex, coin)
        spec = snapshot.markets.get(market.key)
        if spec is None:
            snapshot = await self.get_snapshot(force_refresh=True)
            spec = snapshot.markets.get(market.key)
        if spec is None:
            raise ValueError(f"Unknown Hyperliquid market: {market.canonical_coin}")
        if not spec.is_active or spec.is_delisted or spec.is_halted:
            raise ValueError(
                f"Hyperliquid market is not active: {market.canonical_coin}"
            )
        if spec.mid is None:
            raise ValueError(
                f"Hyperliquid market has no valid price: {market.canonical_coin}"
            )
        return spec

    def cached_snapshot(self) -> RegistrySnapshot | None:
        snapshot = self.__class__._snapshot
        if snapshot is not None:
            return snapshot
        snapshot = self._read_cache()
        if snapshot is not None:
            self.__class__._snapshot = snapshot
        return snapshot

    async def _load_dex(self, dex: str, dex_index: int) -> list[MarketSpec]:
        meta_result, mids = await asyncio.gather(
            self._client.get_meta_and_asset_contexts(dex),
            self._client.get_all_mids(dex),
        )
        meta, asset_contexts = meta_result
        result: list[MarketSpec] = []
        for asset_index, asset in enumerate(meta.universe):
            context = (
                asset_contexts[asset_index] if asset_index < len(asset_contexts) else {}
            )
            canonical = market_id(dex, asset.name).canonical_coin
            mid = mids.get(canonical) or mids.get(asset.name)
            is_halted = bool(context.get("isHalted") or context.get("halted"))
            asset_id = (
                asset_index if not dex else 100_000 + dex_index * 10_000 + asset_index
            )
            result.append(
                MarketSpec(
                    dex=dex,
                    coin=canonical,
                    asset_id=asset_id,
                    sz_decimals=asset.sz_decimals,
                    max_leverage=asset.max_leverage,
                    collateral_token=meta.collateral_token,
                    mid_price=str(mid) if mid is not None else None,
                    is_active=not asset.is_delisted
                    and not is_halted
                    and mid is not None,
                    is_delisted=asset.is_delisted,
                    is_halted=is_halted,
                )
            )
        return result

    @staticmethod
    def _stale(snapshot: RegistrySnapshot) -> bool:
        return snapshot.age_seconds > settings.copy_engine_registry_stale_seconds

    @staticmethod
    def _read_cache() -> RegistrySnapshot | None:
        try:
            raw = get_redis_client().get(_CACHE_KEY)
            if raw is None:
                return None
            data = json.loads(raw)
            markets = {
                key: MarketSpec(**value) for key, value in data["markets"].items()
            }
            return RegistrySnapshot(
                loaded_at=float(data["loaded_at"]),
                dex_names=tuple(data["dex_names"]),
                markets=markets,
            )
        except Exception as exc:
            logger.warning("copy_market_registry_cache_read_failed", error=str(exc))
            return None

    @staticmethod
    def _write_cache(snapshot: RegistrySnapshot) -> None:
        try:
            payload = {
                "loaded_at": snapshot.loaded_at,
                "dex_names": list(snapshot.dex_names),
                "markets": {
                    key: asdict(value) for key, value in snapshot.markets.items()
                },
            }
            get_redis_client().setex(
                _CACHE_KEY,
                settings.copy_engine_registry_stale_seconds,
                json.dumps(payload),
            )
        except Exception as exc:
            logger.warning("copy_market_registry_cache_write_failed", error=str(exc))

from typing import Any

from app.core.logging import get_logger
from app.core.redis_client import get_redis_client

logger = get_logger(__name__)

_METRIC_PREFIX = "copy:v2:metric:"


def increment(name: str, amount: int = 1) -> None:
    try:
        get_redis_client().incrby(_METRIC_PREFIX + name, amount)
    except Exception:
        logger.warning("copy_metric_write_failed", metric=name)


def event(
    name: str,
    *,
    user_id: int | None = None,
    subscription_id: int | None = None,
    dex: str | None = None,
    coin: str | None = None,
    signal_id: int | None = None,
    target_version: int | None = None,
    intent_id: int | None = None,
    cloid: str | None = None,
    oid: int | None = None,
    **details: Any,
) -> None:
    logger.info(
        name,
        user_id=user_id,
        subscription_id=subscription_id,
        dex=dex,
        coin=coin,
        signal_id=signal_id,
        target_version=target_version,
        intent_id=intent_id,
        cloid=cloid,
        oid=oid,
        **details,
    )

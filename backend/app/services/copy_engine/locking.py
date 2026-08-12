import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def advisory_lock_key(*parts: object) -> int:
    """Return a stable signed int64 PostgreSQL advisory-lock key."""
    payload = "\x1f".join(str(part) for part in parts).encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def advisory_xact_lock(db: AsyncSession, *parts: object) -> None:
    key = advisory_lock_key("copy-engine-v2", *parts)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def lock_user_account(db: AsyncSession, user_id: int) -> None:
    await advisory_xact_lock(db, "account", user_id)


async def lock_market(
    db: AsyncSession,
    user_id: int,
    dex: str,
    coin: str,
) -> None:
    await lock_user_account(db, user_id)
    await advisory_xact_lock(db, "market", user_id, dex, coin)

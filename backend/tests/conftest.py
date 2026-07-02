import asyncio
import os
from collections.abc import AsyncGenerator

# Set test env vars BEFORE app imports so pydantic Settings picks them up on first load.
# These override .env file values (os.environ has higher priority in pydantic-settings).
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:test"
os.environ["SECRET_KEY"] = "test-secret-key-min-32-chars-random-string"
os.environ["AGENT_ENCRYPTION_KEY"] = "0" * 64
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://copytrade:copytrade@localhost:5433/copytrade_test"
)
os.environ["HL_SKIP_APPROVE"] = "true"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = (
    "postgresql+asyncpg://copytrade:copytrade@localhost:5433/copytrade_test"
)

# NullPool: every connect() creates a fresh connection, closed immediately on release.
# Prevents asyncpg connections from being pooled across event-loop function boundaries.
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
TestSessionFactory = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(scope="session")
def event_loop():
    """One shared event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Direct test session for seeding / querying in test body only."""
    async with TestSessionFactory() as session:
        yield session


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP test client. get_db is overridden to mirror production semantics:
    commit on success, rollback on exception, NullPool to avoid cross-loop connections.
    """

    async def fresh_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSessionFactory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = fresh_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()

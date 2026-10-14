import os
import sys
from pathlib import Path

# Make the `app` package importable regardless of the directory pytest runs from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Defaults for local test runs (CI and Docker Compose provide their own env).
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/test_urlshortener",
)
os.environ.setdefault("DATABASE_READ_URL", os.environ["DATABASE_URL"])
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest_asyncio
from app import models  # noqa: F401  (register tables on Base.metadata)
from app.cache import cache
from app.database import Base, read_engine, write_engine
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    """Fresh schema + empty cache for every test; dispose pools across event loops."""
    async with write_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("TRUNCATE TABLE click_events, urls RESTART IDENTITY CASCADE")
        )

    redis_client = await cache.get_client()
    await redis_client.flushdb()

    yield

    await write_engine.dispose()
    await read_engine.dispose()
    await cache.close()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

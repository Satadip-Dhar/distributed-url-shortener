from redis.asyncio import Redis

from app.cache import cache
from app.database import get_read_db, get_write_db

__all__ = ["get_read_db", "get_redis", "get_write_db"]


async def get_redis() -> Redis:
    """Dependency that provides a Redis client from the shared connection pool."""
    return await cache.get_client()

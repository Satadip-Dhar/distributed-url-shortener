import redis.asyncio as redis

from app.config import settings


class RedisCache:
    """
    Cache-Aside Pattern Implementation:

    READ:
      1. App checks cache first
      2. Cache HIT -> return cached value
      3. Cache MISS -> query database -> write result to cache -> return

    WRITE:
      1. Write to database
      2. Invalidate cache entry (or write-through)

    Why cache-aside (not write-through/write-behind)?
    - Simpler to implement
    - No stale data risk on write path
    - Perfect for read-heavy workloads (URL shortener = 100:1 read:write)
    """

    def __init__(self):
        self.pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )

    async def get_client(self) -> redis.Redis:
        return redis.Redis(connection_pool=self.pool)

    async def close(self):
        await self.pool.disconnect()


cache = RedisCache()

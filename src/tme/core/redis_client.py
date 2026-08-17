"""Shared async Redis client.

One connection pool for the whole process, used both for the bot-config cache
(:mod:`tme.core.cache`) and for aiogram's FSM storage (:mod:`tme.core.storage`).
"""

from __future__ import annotations

import redis.asyncio as aioredis

from tme.config import settings

# `decode_responses=False` — we store orjson bytes for configs and let aiogram's
# RedisStorage manage its own (string) keyspace. A single pool is shared.
redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=False,
    health_check_interval=30,
)


async def close_redis() -> None:
    """Close the Redis connection pool (call on app shutdown)."""
    await redis_client.aclose()

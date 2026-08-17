"""DB → RAM bot-config cache (the heart of the scalable architecture).

Flow for every incoming update:

1. Router asks :func:`get_bot_config` for a token's config.
2. **Cache hit** → parse the cached JSON from Redis and return immediately.
   No Postgres round-trip. This is the hot path for ~all traffic.
3. **Cache miss** (first request for this bot, or after invalidation) →
   load the row from Postgres, write it to Redis with a TTL, and return it.

Because the config is small JSON, thousands of bots cost only a few MB of Redis
— never a per-bot process or a resident aiogram Bot object graph.
"""

from __future__ import annotations

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.config import settings
from tme.core.logging import get_logger
from tme.core.redis_client import redis_client
from tme.database.engine import session_scope
from tme.database.models import Bot
from tme.schemas.bot_config import BotConfigSchema

logger = get_logger(__name__)

# Sentinel cached for tokens that resolve to no active bot, so a flood of
# updates for a deleted/unknown bot cannot hammer Postgres ("cache penetration").
_NEGATIVE = b"\x00"
_NEGATIVE_TTL = 60


def _cache_key(bot_token: str) -> str:
    return f"botcfg:{bot_token}"


async def _load_from_db(bot_token: str, session: AsyncSession) -> BotConfigSchema | None:
    """Load and validate a bot's config from Postgres, or ``None`` if absent."""
    result = await session.execute(
        select(Bot).where(Bot.token == bot_token, Bot.is_active.is_(True))
    )
    bot = result.scalar_one_or_none()
    if bot is None or bot.config is None:
        return None
    # Validate on the way out of the DB so a corrupt row can't poison the cache.
    return BotConfigSchema.model_validate(bot.config.flow)


async def get_bot_config(bot_token: str) -> BotConfigSchema | None:
    """Return a tenant bot's config, using Redis as a read-through cache.

    Returns ``None`` if the token maps to no active bot (also negatively cached).
    """
    key = _cache_key(bot_token)

    cached = await redis_client.get(key)
    if cached is not None:
        if cached == _NEGATIVE:
            return None
        return BotConfigSchema.model_validate_json(cached)

    # Miss → hit Postgres, then populate Redis.
    async with session_scope() as session:
        config = await _load_from_db(bot_token, session)

    if config is None:
        await redis_client.set(key, _NEGATIVE, ex=_NEGATIVE_TTL)
        logger.debug("Negative-cached unknown/inactive bot token …%s", bot_token[-6:])
        return None

    await redis_client.set(
        key,
        config.model_dump_json().encode(),
        ex=settings.config_cache_ttl,
    )
    logger.debug("Warmed config cache for bot …%s", bot_token[-6:])
    return config


async def set_bot_config(bot_token: str, config: BotConfigSchema) -> None:
    """Write a config straight into the cache (used right after provisioning)."""
    await redis_client.set(
        _cache_key(bot_token),
        config.model_dump_json().encode(),
        ex=settings.config_cache_ttl,
    )


async def invalidate_bot_config(bot_token: str) -> None:
    """Drop a bot's cached config so the next request reloads it from Postgres.

    Call this whenever a config is edited in the management UI.
    """
    await redis_client.delete(_cache_key(bot_token))
    logger.debug("Invalidated config cache for bot …%s", bot_token[-6:])


# orjson is imported for callers that need fast (de)serialisation of arbitrary
# flow payloads outside the schema; re-exported here as a convenience.
__all__ = [
    "get_bot_config",
    "invalidate_bot_config",
    "orjson",
    "set_bot_config",
]

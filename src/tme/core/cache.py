"""DB → RAM bot-config cache (the heart of the scalable architecture).

Flow for every incoming update:

1. Router asks :func:`get_bot_config` for a token's config.
2. **Cache hit** → parse the cached JSON from Redis and return immediately.
   No Postgres round-trip. This is the hot path for ~all traffic.
3. **Cache miss** (first request for this bot, or after invalidation) →
   load the row from Postgres, write it to Redis with a TTL, and return it.

Because the config is small JSON, thousands of bots cost only a few MB of Redis
— never a per-bot process or a resident aiogram Bot object graph.

Since S0.3 activation (issue #28) the cache key is the **token hash**, not the
token: ``botcfg:{token_hash}``. Callers still pass the raw token — hashing
happens here, at the boundary — so no plaintext token is ever a Redis key and
resolution goes through :func:`tme.services.bot_lookup.find_bot_by_token`
(hash first, plaintext fallback while a stack is being backfilled).
"""

from __future__ import annotations

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from tme.config import settings
from tme.core.logging import get_logger
from tme.core.redis_client import redis_client
from tme.database.engine import session_scope
from tme.schemas.bot_config import BotConfigUnion, dump_bot_config, parse_bot_config
from tme.services.bot_lookup import find_bot_by_token
from tme.services.vault import token_hash

logger = get_logger(__name__)

# Sentinel cached for tokens that resolve to no active bot, so a flood of
# updates for a deleted/unknown bot cannot hammer Postgres ("cache penetration").
_NEGATIVE = b"\x00"
_NEGATIVE_TTL = 60


def _cache_key(bot_token: str) -> str:
    """Redis key for a **raw** token: ``botcfg:{token_hash}``."""
    return _cache_key_for_hash(token_hash(bot_token))


def _cache_key_for_hash(token_hash_value: str) -> str:
    """Redis key for an already-computed hash (rows whose plaintext is cleared)."""
    return f"botcfg:{token_hash_value}"


async def _load_from_db(bot_token: str, session: AsyncSession) -> BotConfigUnion | None:
    """Load and validate a bot's config from Postgres, or ``None`` if absent."""
    bot = await find_bot_by_token(session, bot_token, active_only=True)
    if bot is None or bot.config is None:
        return None
    # Validate on the way out of the DB so a corrupt row can't poison the cache.
    return parse_bot_config(bot.config.flow)


async def get_bot_config(bot_token: str) -> BotConfigUnion | None:
    """Return a tenant bot's config, using Redis as a read-through cache.

    Returns ``None`` if the token maps to no active bot (also negatively cached).
    """
    key = _cache_key(bot_token)

    cached = await redis_client.get(key)
    if cached is not None:
        if cached == _NEGATIVE:
            return None
        return parse_bot_config(orjson.loads(cached))

    # Miss → hit Postgres, then populate Redis.
    async with session_scope() as session:
        config = await _load_from_db(bot_token, session)

    if config is None:
        await redis_client.set(key, _NEGATIVE, ex=_NEGATIVE_TTL)
        logger.debug("Negative-cached unknown/inactive bot token …%s", bot_token[-6:])
        return None

    await redis_client.set(
        key,
        dump_bot_config(config),
        ex=settings.config_cache_ttl,
    )
    logger.debug("Warmed config cache for bot …%s", bot_token[-6:])
    return config


async def set_bot_config(bot_token: str, config: BotConfigUnion) -> None:
    """Write a config straight into the cache (used right after provisioning)."""
    await redis_client.set(
        _cache_key(bot_token),
        dump_bot_config(config),
        ex=settings.config_cache_ttl,
    )


async def invalidate_bot_config(bot_token: str) -> None:
    """Drop a bot's cached config so the next request reloads it from Postgres.

    Takes the **raw** token (hashed here) — call this whenever a config is
    edited in the management UI. For a row whose plaintext token has already
    been cleared, use :func:`invalidate_bot_config_hash` with the stored
    ``bots.token_hash``.
    """
    await redis_client.delete(_cache_key(bot_token))
    logger.debug("Invalidated config cache for bot …%s", bot_token[-6:])


async def invalidate_bot_config_hash(token_hash_value: str) -> None:
    """Drop the cache by a precomputed ``bots.token_hash`` (post-backfill rows)."""
    await redis_client.delete(_cache_key_for_hash(token_hash_value))
    logger.debug("Invalidated config cache by token hash %s…", token_hash_value[:8])


# orjson is imported for callers that need fast (de)serialisation of arbitrary
# flow payloads outside the schema; re-exported here as a convenience.
__all__ = [
    "get_bot_config",
    "invalidate_bot_config",
    "invalidate_bot_config_hash",
    "orjson",
    "set_bot_config",
]

"""Per-user language preference (Phase 1 S1.2).

An explicit choice made via the tenant bot's ``/language`` picker overrides
the user's Telegram UI language. Stored in ``user_languages`` (Postgres) with
a Redis read-through cache so the per-update middleware path is a single GET;
an absent row means "follow Telegram's ``language_code``".
"""

from __future__ import annotations

from sqlalchemy import select

from tme.core.logging import get_logger
from tme.core.redis_client import redis_client
from tme.database.engine import session_scope
from tme.database.models import UserLanguage

logger = get_logger(__name__)

#: Sentinel cached for users with no explicit preference (cache penetration).
_NEGATIVE = b"\x00"
_CACHE_TTL = 24 * 3600


def _cache_key(telegram_id: int) -> str:
    return f"userlang:{telegram_id}"


async def get_user_language(telegram_id: int) -> str | None:
    """The user's explicit preference, or ``None`` to follow Telegram's UI."""
    key = _cache_key(telegram_id)
    cached = await redis_client.get(key)
    if cached is not None:
        if cached == _NEGATIVE:
            return None
        return cached.decode() if isinstance(cached, bytes) else cached

    async with session_scope() as session:
        row = await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )
        user_lang = row.scalar_one_or_none()
        code = user_lang.language_code if user_lang is not None else None

    await redis_client.set(key, code.encode() if code else _NEGATIVE, ex=_CACHE_TTL)
    return code


async def set_user_language(telegram_id: int, language_code: str) -> None:
    """Persist an explicit preference (upsert) and refresh the cache."""
    async with session_scope() as session:
        row = await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )
        user_lang = row.scalar_one_or_none()
        if user_lang is None:
            session.add(UserLanguage(telegram_id=telegram_id, language_code=language_code))
        else:
            user_lang.language_code = language_code

    await redis_client.set(_cache_key(telegram_id), language_code.encode(), ex=_CACHE_TTL)
    logger.debug("Set language %s for tg=%s", language_code, telegram_id)


async def clear_user_language(telegram_id: int) -> None:
    """Drop an explicit preference — the user follows Telegram's UI language again."""
    async with session_scope() as session:
        row = await session.execute(
            select(UserLanguage).where(UserLanguage.telegram_id == telegram_id)
        )
        user_lang = row.scalar_one_or_none()
        if user_lang is not None:
            await session.delete(user_lang)

    await redis_client.delete(_cache_key(telegram_id))
    logger.debug("Cleared language preference for tg=%s", telegram_id)

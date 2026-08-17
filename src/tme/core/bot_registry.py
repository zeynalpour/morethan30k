"""Registry of aiogram :class:`Bot` instances.

Design note — why this stays tiny in RAM:

* A ``Bot`` object is a thin wrapper around a token + an HTTP session; it holds
  **no** per-bot handlers or polling loop. All bots share one
  :class:`AiohttpSession` (one TCP connector) and one :class:`Dispatcher`.
* We keep a small LRU of recently-used ``Bot`` objects purely to avoid
  re-instantiating them; evicting one is free because the session is shared and
  owned by the registry, not the individual bot.

This is what lets a single process front 30k+ bots without memory bloat.
"""

from __future__ import annotations

from collections import OrderedDict

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from tme.config import settings

# One HTTP session (connection pool) shared by every Bot instance.
_shared_session = AiohttpSession()
_default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)

# Bounded LRU cache of tenant Bot objects, keyed by token.
_MAX_CACHED_BOTS = 2048
_bot_cache: OrderedDict[str, Bot] = OrderedDict()

# The controller bot is a long-lived singleton.
main_bot: Bot = Bot(
    token=settings.main_bot_token.get_secret_value(),
    session=_shared_session,
    default=_default_props,
)


def get_tenant_bot(token: str) -> Bot:
    """Return a (cached) :class:`Bot` for a tenant token, creating it on demand.

    The returned bot shares the process-wide HTTP session; do **not** close it
    per-request — the registry owns the session's lifecycle.
    """
    bot = _bot_cache.get(token)
    if bot is not None:
        _bot_cache.move_to_end(token)  # mark as most-recently-used
        return bot

    bot = Bot(token=token, session=_shared_session, default=_default_props)
    _bot_cache[token] = bot
    if len(_bot_cache) > _MAX_CACHED_BOTS:
        _bot_cache.popitem(last=False)  # evict least-recently-used
    return bot


async def close_registry() -> None:
    """Close the shared HTTP session and the main bot (call on shutdown)."""
    _bot_cache.clear()
    await _shared_session.close()

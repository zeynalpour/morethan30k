"""Liveness detection for tenant bots (deleted-in-BotFather sync).

Telegram never tells a manager bot that a managed bot was deleted in
BotFather: ``ManagedBotUpdated`` only fires on *creation, token update, or
owner update*. What does change is the token — a deleted bot's token is
revoked, so ``getMe`` with it is rejected. That rejection is the signal this
module probes for, with a short Redis cache so dashboard loads don't hammer
the Bot API.

Failure policy: **only an explicit rejection marks a bot dead**. Network
errors, timeouts and Telegram-side outages fail *open* (reported alive) — a
transient blip must never archive a healthy bot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from aiogram import Bot as AioBot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNotFound,
    TelegramUnauthorizedError,
)

from tme.core.logging import get_logger
from tme.core.redis_client import redis_client

logger = get_logger(__name__)

#: How long a liveness verdict is trusted, in seconds (dashboard-load cadence).
LIVENESS_TTL = 180

#: Fan-out cap when probing a whole bot list — stay polite to the Bot API.
MAX_CONCURRENT_PROBES = 5

_ALIVE = b"\x01"
_DEAD = b"\x00"


def _cache_key(bot_id: int) -> str:
    return f"botlive:{bot_id}"


async def probe_token(token: str) -> bool:
    """Ask Telegram whether this tenant token still exists (``getMe``).

    ``False`` only on an explicit rejection (401 Unauthorized / 404 Not
    Found). Every other failure fails open as ``True``.
    """
    probe = AioBot(token=token)
    try:
        async with probe:
            await probe.get_me()
        return True
    except (TelegramUnauthorizedError, TelegramNotFound):
        return False
    except TelegramAPIError as exc:
        logger.warning("Liveness probe inconclusive for …%s: %s", token[-6:], exc)
        return True
    except Exception as exc:  # fail-open is deliberate
        logger.warning("Liveness probe failed for …%s: %s", token[-6:], exc)
        return True


async def bot_is_alive(bot_id: int, token: str) -> bool:
    """Cached liveness verdict for one bot (:data:`LIVENESS_TTL` seconds)."""
    key = _cache_key(bot_id)
    cached = await redis_client.get(key)
    if cached is not None:
        return cached == _ALIVE

    alive = await probe_token(token)
    await redis_client.set(key, _ALIVE if alive else _DEAD, ex=LIVENESS_TTL)
    if not alive:
        logger.info("Bot id=%s was deleted in Telegram (token revoked) — archiving", bot_id)
    return alive


async def probe_bots(bots: Iterable[tuple[int, str]]) -> dict[int, bool]:
    """Probe many ``(bot_id, token)`` pairs with a bounded fan-out."""
    pairs = list(bots)
    if not pairs:
        return {}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

    async def _one(bot_id: int, token: str) -> tuple[int, bool]:
        async with semaphore:
            return bot_id, await bot_is_alive(bot_id, token)

    return dict(await asyncio.gather(*(_one(bot_id, token) for bot_id, token in pairs)))


async def forget_bot_liveness(bot_id: int) -> None:
    """Drop a cached verdict (bot removed from TME)."""
    await redis_client.delete(_cache_key(bot_id))
    logger.debug("Dropped liveness verdict for bot id=%s", bot_id)

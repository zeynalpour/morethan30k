"""Outer middleware that injects a tenant bot's cached config into every update.

Runs once per update on the **tenant** dispatcher, before any filtering. It:

1. Reads the incoming :class:`~aiogram.Bot` from the handler data.
2. Resolves that bot's config via the Redis read-through cache.
3. Injects it as ``bot_config`` so downstream handlers receive it for free.

If the token maps to no active bot (``None``), the update is silently dropped —
we never process traffic for an unknown/deactivated tenant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject

from tme.core.cache import get_bot_config
from tme.core.logging import get_logger

logger = get_logger(__name__)


class ConfigMiddleware(BaseMiddleware):
    """Load ``bot_config`` from Redis and attach it to the handler data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data["bot"]

        config = await get_bot_config(bot.token)
        if config is None:
            # Unknown or inactive tenant — drop without invoking any handler.
            logger.debug("Dropping update for unresolved bot id=%s", bot.id)
            return None

        data["bot_config"] = config
        return await handler(event, data)

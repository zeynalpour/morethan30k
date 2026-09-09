"""Resolves each user's effective language and injects it into handler data.

Runs once per update on the **tenant** dispatcher, after ``ConfigMiddleware``
so it can skip the lookup for single-language bots. Resolution order (Phase 1
S1.2): the user's explicit ``/language`` preference → Telegram UI
``language_code`` → ``None`` (full fallback chain in :func:`localize`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from tme.core.i18n import effective_language
from tme.schemas.bot_config import BotConfigUnion
from tme.services.user_language import get_user_language


class I18nMiddleware(BaseMiddleware):
    """Attach the effective ``language_code`` to every tenant update."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        config: BotConfigUnion | None = data.get("bot_config")

        # Single-language bots never localize — skip the cache/DB lookup.
        if config is not None and config.single_language:
            data["language_code"] = None
            return await handler(event, data)

        telegram_code: str | None = None
        user_id: int | None = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user is not None:
            user_id = event.from_user.id
            telegram_code = event.from_user.language_code

        stored = await get_user_language(user_id) if user_id is not None else None
        data["language_code"] = effective_language(stored, telegram_code)
        return await handler(event, data)

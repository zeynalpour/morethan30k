"""Resolves each user's effective language and injects it into handler data.

Runs once per update on the **tenant** dispatcher, after ``ConfigMiddleware``
so it can skip the lookup for single-language bots. Resolution order (Phase 1
S1.2): the user's explicit ``/language`` preference → Telegram UI
``language_code`` → ``None`` (full fallback chain in :func:`localize`).

**Note on event type:** this middleware is registered on ``dp.update``, so it
receives the :class:`~aiogram.types.Update` wrapper — NOT the inner
Message/CallbackQuery. Extract the user from the wrapper explicitly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User

from tme.core.i18n import effective_language
from tme.schemas.bot_config import BotConfigUnion
from tme.services.user_language import get_user_language

#: Update fields that can carry a `from_user` (message-like / callback).
_USER_BEARING_FIELDS = ("message", "edited_message", "channel_post", "callback_query")


def _event_user(event: TelegramObject) -> User | None:
    """The user behind an update, whatever the wrapper shape."""
    if isinstance(event, (Message, CallbackQuery)):
        return event.from_user
    if isinstance(event, Update):
        for attr in _USER_BEARING_FIELDS:
            inner = getattr(event, attr, None)
            if inner is not None and getattr(inner, "from_user", None) is not None:
                return inner.from_user
    return None


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

        user = _event_user(event)
        stored = await get_user_language(user.id) if user is not None else None
        telegram_code = user.language_code if user is not None else None
        data["language_code"] = effective_language(stored, telegram_code)
        return await handler(event, data)

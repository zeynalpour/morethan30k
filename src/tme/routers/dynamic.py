"""The dynamic tenant router — a minimal JSON *flow engine*.

There is **one** router instance shared by every tenant bot. It contains no
hard-coded copy; instead each handler reads the per-bot ``bot_config`` that
:class:`tme.middlewares.config_middleware.ConfigMiddleware` injected from Redis.
That is what makes one codebase behave like thousands of distinct bots.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from tme.core.logging import get_logger
from tme.schemas.bot_config import BotConfigSchema

logger = get_logger(__name__)

dynamic_router = Router(name="dynamic_tenant")


def _build_menu(config: BotConfigSchema) -> InlineKeyboardMarkup | None:
    """Render a tenant's ``menu_buttons`` into an inline keyboard (one per row)."""
    if not config.menu_buttons:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for btn in config.menu_buttons:
        if btn.url:
            rows.append([InlineKeyboardButton(text=btn.text, url=btn.url)])
        else:
            # callback may be None → fall back to a stable no-op token.
            rows.append([InlineKeyboardButton(text=btn.text, callback_data=btn.callback or "noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dynamic_router.message(CommandStart())
async def on_start(message: Message, bot_config: BotConfigSchema) -> None:
    """Reply to /start with the tenant's configured welcome + menu."""
    await message.answer(
        text=bot_config.welcome_message,
        reply_markup=_build_menu(bot_config),
    )


@dynamic_router.callback_query(F.data)
async def on_menu_click(callback: CallbackQuery, bot_config: BotConfigSchema) -> None:
    """Handle a menu button press.

    MVP behaviour: echo which node was selected and re-render the menu. A richer
    flow engine would look ``callback.data`` up in ``bot_config`` to find the
    next node (nested messages, forms, etc.).
    """
    data = callback.data or ""
    # Try to show the label of the button that was pressed, for a nicer reply.
    label = next((b.text for b in bot_config.menu_buttons if b.callback == data), data)

    await callback.answer()  # stop Telegram's loading spinner
    if callback.message is not None:
        await callback.message.answer(
            text=f"You selected: <b>{label}</b>",
            reply_markup=_build_menu(bot_config),
        )


@dynamic_router.message()
async def on_fallback(message: Message, bot_config: BotConfigSchema) -> None:
    """Any other message → the tenant's configured fallback text."""
    await message.answer(bot_config.fallback_message)

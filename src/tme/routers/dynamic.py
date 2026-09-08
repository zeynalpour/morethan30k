"""The dynamic tenant router — a minimal JSON *flow engine*.

There is **one** router instance shared by every tenant bot. It contains no
hard-coded copy; instead each handler reads the per-bot ``bot_config`` that
:class:`tme.middlewares.config_middleware.ConfigMiddleware` injected from Redis.
That is what makes one codebase behave like thousands of distinct bots.

The router dispatches on :attr:`bot_config.bot_type` (the discriminant parsed by
:func:`tme.schemas.bot_config.parse_bot_config`): generic tenants get the familiar
welcome+menu flow, Hello bots greet by name, and Echo bots bounce every
non-command message back.
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
from tme.schemas.bot_config import BotConfigUnion, EchoBotConfig, HelloBotConfig

logger = get_logger(__name__)

dynamic_router = Router(name="dynamic_tenant")


def _build_menu(config: BotConfigUnion) -> InlineKeyboardMarkup | None:
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


def _greeting_for(config: BotConfigUnion) -> str:
    """Return the text to send on /start for a given config.

    Hellos use their dedicated greeting; everything else (generic, echo, bridge,
    ai_gateway, legacy) falls back to ``welcome_message``.
    """
    if isinstance(config, HelloBotConfig):
        return config.greeting
    return config.welcome_message


@dynamic_router.message(CommandStart())
async def on_start(message: Message, bot_config: BotConfigUnion) -> None:
    """Reply to /start with the tenant's configured greeting + menu."""
    await message.answer(
        text=_greeting_for(bot_config),
        reply_markup=_build_menu(bot_config),
    )


@dynamic_router.callback_query(F.data)
async def on_menu_click(callback: CallbackQuery, bot_config: BotConfigUnion) -> None:
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
async def on_fallback(message: Message, bot_config: BotConfigUnion) -> None:
    """Any non-menu message → dispatch by tenant type.

    - Echo   : mirror the user's text back (with optional prefix).
    - Hello  : re-greet once again (keeps the bot on-message).
    - default: the configured fallback text.
    """
    if isinstance(bot_config, EchoBotConfig):
        prefix = bot_config.echo_prefix
        await message.answer(f"{prefix}{message.text or message.caption or ''}")
    elif isinstance(bot_config, HelloBotConfig):
        await message.answer(bot_config.greeting)
    else:
        await message.answer(bot_config.fallback_message)

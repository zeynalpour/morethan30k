"""The dynamic tenant router — a minimal JSON *flow engine*.

There is **one** router instance shared by every tenant bot. It contains no
hard-coded copy; instead each handler reads the per-bot ``bot_config`` that
:class:`tme.middlewares.config_middleware.ConfigMiddleware` injected from Redis.
That is what makes one codebase behave like thousands of distinct bots.

The router dispatches on :attr:`bot_config.bot_type` (the discriminant parsed by
:func:`tme.schemas.bot_config.parse_bot_config`): generic tenants get the familiar
welcome+menu flow, Hello bots greet by name, and Echo bots bounce every
non-command message back. Since Phase 1, all user-facing copy goes through
:func:`tme.core.i18n.localize`, so each user sees the bot in their own
Telegram language (fallback: English, then the bot's base language).
"""

from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from tme.core.i18n import localize
from tme.core.logging import get_logger
from tme.schemas.bot_config import BotConfigUnion, EchoBotConfig, HelloBotConfig, MenuButton
from tme.services.user_language import set_user_language

logger = get_logger(__name__)

dynamic_router = Router(name="dynamic_tenant")

#: (code, flag label) pairs offered by the /language picker (roadmap list).
_LANGUAGE_FLAGS: list[tuple[str, str]] = [
    ("en", "🇬🇧 English"),
    ("fa", "🇮🇷 فارسی"),
    ("de", "🇩🇪 Deutsch"),
    ("ru", "🇷🇺 Русский"),
    ("ar", "🇸🇦 العربية"),
    ("es", "🇪🇸 Español"),
    ("fr", "🇫🇷 Français"),
    ("tr", "🇹🇷 Türkçe"),
    ("zh", "🇨🇳 中文"),
    ("hi", "🇮🇳 हिन्दी"),
    ("id", "🇮🇩 Bahasa Indonesia"),
    ("pt", "🇧🇷 Português"),
]

_LANG_PREFIX = "lang:"


def _build_menu(menu_buttons: list[MenuButton]) -> InlineKeyboardMarkup | None:
    """Render ``menu_buttons`` into an inline keyboard (one per row)."""
    if not menu_buttons:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for btn in menu_buttons:
        if btn.url:
            rows.append([InlineKeyboardButton(text=btn.text, url=btn.url)])
        else:
            # callback may be None → fall back to a stable no-op token.
            rows.append([InlineKeyboardButton(text=btn.text, callback_data=btn.callback or "noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _language_keyboard() -> InlineKeyboardMarkup:
    """Inline flag picker for the /language command (two per row)."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(_LANGUAGE_FLAGS), 2):
        row = [
            InlineKeyboardButton(text=flag, callback_data=f"{_LANG_PREFIX}{code}")
            for code, flag in _LANGUAGE_FLAGS[i : i + 2]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dynamic_router.message(CommandStart())
async def on_start(message: Message, bot_config: BotConfigUnion, language_code: str | None) -> None:
    """Reply to /start with the tenant's greeting + menu, in the user's language."""
    copy = localize(bot_config, language_code)
    # Hellos use their dedicated greeting; everything else the welcome message.
    text = copy.greeting if isinstance(bot_config, HelloBotConfig) else copy.welcome_message
    await message.answer(text=text, reply_markup=_build_menu(copy.menu_buttons))


@dynamic_router.message(Command("language", "lang"))
async def on_language_command(message: Message) -> None:
    """Show the inline flag picker to override the user's Telegram language."""
    await message.answer("🌐 Choose your language:", reply_markup=_language_keyboard())


@dynamic_router.callback_query(F.data.startswith(_LANG_PREFIX))
async def on_language_pick(callback: CallbackQuery) -> None:
    """Persist the user's explicit language choice for every bot they use."""
    if callback.from_user is None:
        await callback.answer("Something went wrong — please try again.")
        return
    code = (callback.data or _LANG_PREFIX).removeprefix(_LANG_PREFIX)
    await set_user_language(callback.from_user.id, code)
    flag = next((f for c, f in _LANGUAGE_FLAGS if c == code), code)
    await callback.answer(f"Language set to {code} ✅")
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(f"🌐 Language: {flag}")


@dynamic_router.callback_query(F.data, ~F.data.startswith(_LANG_PREFIX))
async def on_menu_click(
    callback: CallbackQuery, bot_config: BotConfigUnion, language_code: str | None
) -> None:
    """Handle a menu button press.

    MVP behaviour: echo which node was selected and re-render the menu. A richer
    flow engine would look ``callback.data`` up in ``bot_config`` to find the
    next node (nested messages, forms, etc.). Labels come from the user's
    localized copy; ``callback_data`` itself is language-independent.
    """
    data = callback.data or ""
    copy = localize(bot_config, language_code)
    # Try to show the label of the button that was pressed, for a nicer reply.
    label = next((b.text for b in copy.menu_buttons if b.callback == data), data)

    await callback.answer()  # stop Telegram's loading spinner
    if callback.message is not None:
        await callback.message.answer(
            text=f"You selected: <b>{label}</b>",
            reply_markup=_build_menu(copy.menu_buttons),
        )


@dynamic_router.message()
async def on_fallback(
    message: Message, bot_config: BotConfigUnion, language_code: str | None
) -> None:
    """Any non-menu message → dispatch by tenant type (localized).

    - Echo   : mirror the user's text back (with optional prefix).
    - Hello  : re-greet once again (keeps the bot on-message).
    - default: the configured fallback text.
    """
    copy = localize(bot_config, language_code)
    if isinstance(bot_config, EchoBotConfig):
        prefix = copy.echo_prefix
        await message.answer(f"{prefix}{message.text or message.caption or ''}")
    elif isinstance(bot_config, HelloBotConfig):
        await message.answer(copy.greeting)
    else:
        await message.answer(copy.fallback_message)

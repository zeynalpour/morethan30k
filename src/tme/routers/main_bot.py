"""The Main / Controller bot router.

This is the bot users talk to in order to create and manage their own tenant
bots. It runs on its own dispatcher (``main_dp``) — kept separate from the
tenant dispatcher so management commands can never leak into cloned bots.

``managed_bot`` updates (creation / token change / owner change of a bot
managed by the Main Bot) are handled natively via ``@main_router.managed_bot()``
with aiogram's typed ``ManagedBotUpdated`` — the gateway routes every update
through ``main_dp``.

S1.3: all user-facing copy is localized via :mod:`tme.core.main_i18n` — the
owner's stored ``/language`` preference (shared with tenant bots) wins over
their Telegram UI language. The ``I18nMiddleware`` on ``main_dp`` injects
``language_code`` into handlers.
"""

from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.methods import GetManagedBotToken
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestManagedBot,
    ManagedBotUpdated,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from tme.config import settings
from tme.core.i18n import effective_language
from tme.core.logging import get_logger
from tme.core.main_i18n import MAIN_BOT_STRINGS, supported_languages, tr
from tme.database.models import Bot as BotModel
from tme.schemas.bot_config import BotConfigUnion
from tme.services.dashboard import list_bots_for_owner
from tme.services.managed_bots import provision_managed_bot
from tme.services.user_language import (
    clear_user_language,
    get_user_language,
    set_user_language,
)
from tme.templates import TemplateSpec, get_template, list_templates

logger = get_logger(__name__)

main_router = Router(name="main_controller")

_CREATE_BOT = "create_bot"
_LANG_PREFIX = "lang:"

# --- Managed-bot template picker ----------------------------------------------
# Phase 2 S2.2: the creation flow's front door is the template gallery. Both
# the picker keyboard and the callback handler derive from the S2.1 registry
# (`list_templates()`), the same single-source pattern `_TYPE_CHOICES` had —
# so adding a template = adding a `TemplateSpec` constant + one `tmpl.{id}`
# copy key per language. No handler edit (data-only picker).
#
# Callback data is `tmpl:{template_id}` — the version is deliberately NOT in
# the callback: a fresh creation always takes `list_templates()` = latest.
# Template ids are `^[a-z0-9_]{2,32}$`, so the payload stays far below
# Telegram's 64-byte `callback_data` cap.
_TMPL_PREFIX = "tmpl:"
#: Reserved callback value for the expert path — a plain generic starter with
#: today's bare default (no template, no provenance stamp). Deliberately not
#: a registry id: "start from scratch" must never appear in `list_templates()`.
_SCRATCH_ID = "scratch"

#: Tokens of freshly-created managed bots awaiting their owner's template
#: choice, keyed by owner Telegram id. In-memory on purpose (single-process gateway);
#: a restart drops pendings harmlessly — the unprovisioned bot simply never
#: gets a webhook until recreated. A second creation while one is pending
#: replaces the first (the abandoned bot stays unprovisioned).
_PENDING: dict[int, str] = {}


def main_bot_commands(language_code: str | None = None) -> list[BotCommand]:
    """Commands shown in the controller bot's menu (localized descriptions)."""
    return [
        BotCommand(command="start", description=tr(language_code, "cmd.start")),
        BotCommand(command="mybots", description=tr(language_code, "cmd.mybots")),
        BotCommand(command="dashboard", description=tr(language_code, "cmd.dashboard")),
        BotCommand(command="language", description=tr(language_code, "cmd.language")),
    ]


def _dashboard_url(bot_id: int | None = None) -> str:
    """Mini App URL — the home page (all the owner's bots) or one bot's page."""
    base = f"{settings.webhook_base_url}/dashboard/"
    return f"{base}?bid={bot_id}" if bot_id is not None else base


def _dashboard_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    """'Open Mini App' button launching the dashboard home."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language_code, "btn.open_dashboard"),
                    web_app=WebAppInfo(url=_dashboard_url()),
                )
            ]
        ]
    )


async def register_main_commands(bot: Bot) -> None:
    """Push the command menu via setMyCommands (no manual BotFather setup).

    Registers the default (English) menu plus one localized menu per
    supported language — Telegram shows users the menu matching their UI
    language automatically.
    """
    try:
        await bot.set_my_commands(main_bot_commands())
        for code in supported_languages():
            if code == "en":
                continue
            with suppress(TelegramAPIError):
                await bot.set_my_commands(main_bot_commands(code), language_code=code)
        logger.info("Registered main-bot command menu")
    except TelegramAPIError as exc:
        # Non-fatal: startup continues without the menu.
        logger.warning("Failed to register command menu: %s", exc)


def _create_bot_keyboard(language_code: str | None = None) -> ReplyKeyboardMarkup:
    """Keyboard that triggers Telegram's managed bot creation dialog."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr(language_code, "btn.create_bot"),
                    request_managed_bot=KeyboardButtonRequestManagedBot(request_id=1),
                )
            ],
            [KeyboardButton(text=tr(language_code, "btn.my_bots"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _template_button(
    language_code: str | None,
    spec: TemplateSpec,
) -> InlineKeyboardButton:
    """One picker card: localized label (registry display_name as fallback).

    ``tr()`` already falls back to English for untranslated controller
    languages; the ``display_name`` fallback covers a template registered
    before its ``tmpl.{id}`` copy key landed — the picker must never crash
    over a missing string.
    """
    try:
        label = tr(language_code, f"tmpl.{spec.id}")
    except KeyError:
        label = spec.display_name
    return InlineKeyboardButton(
        text=label,
        callback_data=f"{_TMPL_PREFIX}{spec.id}",
    )


def _template_picker_keyboard(language_code: str | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard offering one card per registry template + scratch.

    Data-only: derived from ``list_templates()`` (registration order), so a
    new ``TemplateSpec`` constant appears here without a handler edit. The
    trailing "start from scratch" card preserves today's bare generic default
    for experts — the Hello World / Echo cards subsume the old type buttons.
    """
    rows = [[_template_button(language_code, spec)] for spec in list_templates()]
    rows.append(
        [
            InlineKeyboardButton(
                text=tr(language_code, "tmpl.scratch"),
                callback_data=f"{_TMPL_PREFIX}{_SCRATCH_ID}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _language_keyboard() -> InlineKeyboardMarkup:
    """Picker offering exactly the languages the controller copy ships in."""
    rows = [
        [
            InlineKeyboardButton(
                text=tr(None, f"lang_name.{code}"),
                callback_data=f"{_LANG_PREFIX}{code}",
            )
        ]
        for code in supported_languages()
    ]
    rows.append(
        [InlineKeyboardButton(text=tr(None, "btn.lang_auto"), callback_data=f"{_LANG_PREFIX}auto")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@main_router.message(CommandStart())
async def controller_start(message: Message, language_code: str | None = None) -> None:
    name = message.from_user.first_name if message.from_user else "there"
    await message.answer(
        tr(language_code, "start.welcome", name=name),
        reply_markup=_create_bot_keyboard(language_code),
    )


@main_router.callback_query(F.data == _CREATE_BOT)
async def on_create_bot(callback: CallbackQuery, language_code: str | None = None) -> None:
    """Kick off managed-bot creation.

    Triggered by the inline callback button; the ``KeyboardButton`` with
    ``request_managed_bot`` usually carries the flow already. Once the user
    authorises, Telegram sends a ``managed_bot`` update, which
    ``@main_router.managed_bot()`` provisions — no further action is needed
    here.
    """
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            tr(language_code, "create.intro"),
        )
    user_id = callback.from_user.id if callback.from_user else "?"
    logger.info("User %s initiated managed-bot creation", user_id)


@main_router.managed_bot()
async def on_managed_bot(
    event: ManagedBotUpdated,
    bot: Bot,
) -> None:
    owner_id = event.user.id
    managed_bot_id = event.bot_user.id
    username = getattr(event.bot_user, "username", None)

    logger.info(
        "ManagedBotUpdated: owner=%s managed_bot_id=%s username=@%s",
        owner_id,
        managed_bot_id,
        username,
    )

    # The middleware can't see the user inside a ManagedBotUpdated wrapper —
    # resolve the owner's language explicitly (shared with tenant bots).
    stored = await get_user_language(owner_id)
    language = effective_language(stored, event.user.language_code)

    try:
        token: str = await bot(GetManagedBotToken(user_id=managed_bot_id))
    except TelegramAPIError as exc:
        logger.error("getManagedBotToken failed: %s", exc)
        await bot.send_message(
            chat_id=owner_id,
            text=tr(language, "token_failed"),
        )
        return

    # Hold the token until the owner picks a template; the picker's
    # callback handler does the actual provisioning.
    _PENDING[owner_id] = token
    await bot.send_message(
        chat_id=owner_id,
        text=tr(language, "bot_ready"),
        reply_markup=_template_picker_keyboard(language),
    )


@main_router.callback_query(F.data.startswith(_TMPL_PREFIX))
async def on_pick_template(
    callback: CallbackQuery, bot: Bot, language_code: str | None = None
) -> None:
    """Provision the pending managed bot from the owner's chosen card.

    ``tmpl:{id}`` resolves the registry's latest version (fresh creations
    never pin — the callback deliberately carries no version). The reserved
    ``tmpl:scratch`` card provisions today's bare generic default, preserving
    the pre-Phase-2 no-template path exactly.
    """
    if callback.from_user is None:
        with suppress(TelegramBadRequest):
            await callback.answer(tr(language_code, "err.generic"))
        return

    # The router filter guarantees a non-empty data, but the model types it
    # optional — normalize for the type checker.
    raw = callback.data or ""
    template_id = raw.removeprefix(_TMPL_PREFIX)

    seed: BotConfigUnion | None
    if template_id == _SCRATCH_ID:
        seed = None  # start from scratch → today's bare per-type default
    else:
        try:
            spec = get_template(template_id)  # latest version (never pinned)
        except KeyError:
            # Unknown/stale id (registry edit, deleted template, forged
            # callback) — answer gracefully, keep the token pending.
            with suppress(TelegramBadRequest):
                await callback.answer(tr(language_code, "err.unknown_template"))
            return
        seed = spec.seed

    token = _PENDING.pop(callback.from_user.id, None)
    if token is None:
        with suppress(TelegramBadRequest):
            await callback.answer(tr(language_code, "err.expired"))
        return

    with suppress(TelegramBadRequest):
        await callback.answer()
    try:
        # `bot_type` stays at its GENERIC default: with a seed, the row's
        # type comes from the seed's own discriminator (invariant held in
        # provision_managed_bot); scratch keeps today's generic default.
        bot_row = await provision_managed_bot(
            token=token,
            owner_telegram_id=callback.from_user.id,
            owner_username=callback.from_user.username,
            owner_first_name=callback.from_user.first_name,
            seed=seed,
        )
    except Exception:
        logger.exception("provision_managed_bot failed for owner=%s", callback.from_user.id)
        # Provisioning is idempotent on token — put it back so the owner can
        # simply re-pick instead of recreating the bot from scratch.
        _PENDING[callback.from_user.id] = token
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=tr(language_code, "err.provision"),
        )
        return

    display = f"@{bot_row.username}" if bot_row.username else "your bot"
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=tr(language_code, "bot_live", display=display),
        reply_markup=_settings_keyboard(bot_row.id, language_code),
    )


def _settings_keyboard(bot_id: int, language_code: str | None = None) -> InlineKeyboardMarkup:
    """Inline Mini App button opening one bot's settings page."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(language_code, "btn.open_settings"),
                    web_app=WebAppInfo(url=_dashboard_url(bot_id)),
                )
            ]
        ]
    )


def _bots_keyboard(bots: list[BotModel], language_code: str | None = None) -> InlineKeyboardMarkup:
    """One Mini App button per owned bot, opening that bot's settings."""
    rows = []
    for bot in bots:
        display = (
            f"@{bot.username}"
            if bot.username
            else tr(language_code, "bot.fallback_name", id=bot.id)
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⚙️ {display}",
                    web_app=WebAppInfo(url=_dashboard_url(bot.id)),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_my_bots(bot: Bot, owner_id: int, language_code: str | None = None) -> None:
    """Send the owner's bot list with a settings button per bot."""
    bots = await list_bots_for_owner(owner_telegram_id=owner_id)
    if not bots:
        await bot.send_message(
            chat_id=owner_id,
            text=tr(language_code, "no_bots"),
        )
        return
    await bot.send_message(
        chat_id=owner_id,
        text=tr(language_code, "bots_list", count=len(bots)),
        reply_markup=_bots_keyboard(bots, language_code),
    )


@main_router.message(Command("mybots"))
@main_router.message(F.text.in_(["My Bots", "🤖 My Bots", "🤖 ربات‌های من"]))
async def on_my_bots(message: Message, bot: Bot, language_code: str | None = None) -> None:
    """List the owner's bots (works for bots created before this feature too)."""
    if message.from_user is None:
        return
    await _show_my_bots(bot, message.from_user.id, language_code)


@main_router.message(Command("dashboard"))
async def on_dashboard_command(message: Message, language_code: str | None = None) -> None:
    """Send the 'Open Mini App' button for the dashboard home page."""
    await message.answer(
        tr(language_code, "dashboard.intro"),
        reply_markup=_dashboard_keyboard(language_code),
    )


@main_router.message(Command("language", "lang"))
async def on_language_command(message: Message) -> None:
    """Offer the languages the controller copy ships in (+ Auto reset)."""
    await message.answer(tr(None, "lang.prompt"), reply_markup=_language_keyboard())


@main_router.callback_query(F.data.startswith(_LANG_PREFIX))
async def on_language_pick(callback: CallbackQuery) -> None:
    """Persist the owner's language choice (shared with every tenant bot)."""
    if callback.from_user is None:
        return
    code = (callback.data or "").removeprefix(_LANG_PREFIX)
    if code == "auto":
        await clear_user_language(callback.from_user.id)
        await callback.answer(tr(None, "lang.auto"))
        if isinstance(callback.message, Message):
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    tr(None, "lang.choice", label=tr(None, "btn.lang_auto"))
                )
        return
    if code not in MAIN_BOT_STRINGS:
        await callback.answer(tr(None, "err.generic"))
        return
    await set_user_language(callback.from_user.id, code)
    await callback.answer(tr(None, "lang.done"))
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(
                tr(None, "lang.choice", label=tr(None, f"lang_name.{code}"))
            )


@main_router.message(F.managed_bot_created)
async def on_managed_bot_created_message(message: Message) -> None:
    """Service message confirming the bot creation was initiated."""
    # This fires before the ManagedBotUpdated update arrives.
    # No action needed — provisioning happens in on_managed_bot_updated.
    logger.info("ManagedBotCreated service message received")

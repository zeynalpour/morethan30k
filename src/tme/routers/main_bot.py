"""The Main / Controller bot router.

This is the bot users talk to in order to create and manage their own tenant
bots. It runs on its own dispatcher (``main_dp``) — kept separate from the
tenant dispatcher so management commands can never leak into cloned bots.

``managed_bot`` updates (creation / token change / owner change of a bot
managed by the Main Bot) are handled natively via ``@main_router.managed_bot()``
with aiogram's typed ``ManagedBotUpdated`` — the gateway routes every update
through ``main_dp``.
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
)

from tme.config import settings
from tme.core.logging import get_logger
from tme.database.models import Bot as BotModel, BotType
from tme.services.dashboard import create_dashboard_token_for_owner, list_bots_for_owner
from tme.services.managed_bots import provision_managed_bot

logger = get_logger(__name__)

main_router = Router(name="main_controller")

_CREATE_BOT = "create_bot"

# --- Managed-bot type picker -------------------------------------------------
# Callback-data suffix → (button caption, tenant behaviour type). Both the
# picker keyboard and the callback handler derive from this single source so
# they can never drift when a new type is added.
_PICK_PREFIX = "pick:"
_TYPE_CHOICES: dict[str, tuple[str, BotType]] = {
    "generic": ("🧩 Generic", BotType.GENERIC),
    "hello": ("👋 Hello", BotType.HELLO),
    "echo": ("🔁 Echo", BotType.ECHO),
}

#: Tokens of freshly-created managed bots awaiting their owner's type choice,
#: keyed by owner Telegram id. In-memory on purpose (single-process gateway);
#: a restart drops pendings harmlessly — the unprovisioned bot simply never
#: gets a webhook until recreated. A second creation while one is pending
#: replaces the first (the abandoned bot stays unprovisioned).
_PENDING: dict[int, str] = {}

#: Callback-data prefix for the settings-dashboard button ("settings:{bot_id}").
_SETTINGS_PREFIX = "settings:"


def main_bot_commands() -> list[BotCommand]:
    """Commands shown in the controller bot's menu (auto-registered at startup)."""
    return [
        BotCommand(command="start", description="Start"),
        BotCommand(command="mybots", description="My bots & settings"),
    ]


async def register_main_commands(bot: Bot) -> None:
    """Push the command menu via setMyCommands (no manual BotFather setup)."""
    try:
        await bot.set_my_commands(main_bot_commands())
        logger.info("Registered main-bot command menu")
    except TelegramAPIError as exc:
        # Non-fatal: startup continues without the menu.
        logger.warning("Failed to register command menu: %s", exc)


def _create_bot_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard that triggers Telegram's managed bot creation dialog."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="➕ Create a Managed Bot",
                    request_managed_bot=KeyboardButtonRequestManagedBot(request_id=1),
                )
            ],
            [KeyboardButton(text="🤖 My Bots")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _type_picker_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard offering the tenant behaviour types (from _TYPE_CHOICES)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=display,
                    callback_data=f"{_PICK_PREFIX}{label}",
                )
                for label, (display, _) in _TYPE_CHOICES.items()
            ]
        ]
    )


@main_router.message(CommandStart())
async def controller_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "there"
    await message.answer(
        f"👋 Hi {name}! Welcome to TME.\n\nTap the button below to create your own Telegram bot.",
        reply_markup=_create_bot_keyboard(),
    )


@main_router.callback_query(F.data == _CREATE_BOT)
async def on_create_bot(callback: CallbackQuery) -> None:
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
            "🛠️ <b>Let's create your bot.</b>\n\n"
            "Please authorise TME to create a managed bot for your account. "
            "As soon as you confirm, your new bot will be provisioned and go "
            "live automatically — I'll message you here when it's ready."
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

    try:
        token: str = await bot(GetManagedBotToken(user_id=managed_bot_id))
    except TelegramAPIError as exc:
        logger.error("getManagedBotToken failed: %s", exc)
        await bot.send_message(
            chat_id=owner_id,
            text=(
                "⚠️ I couldn't fetch your new bot's token right now. "
                "Please try again in a few seconds — it may still be propagating "
                "on Telegram's side."
            ),
        )
        return

    # Hold the token until the owner picks a behaviour type; the picker's
    # callback handler does the actual provisioning.
    _PENDING[owner_id] = token
    await bot.send_message(
        chat_id=owner_id,
        text=("🤖 Your new bot is ready!\n\nPick its behaviour — you can change it later:"),
        reply_markup=_type_picker_keyboard(),
    )


@main_router.callback_query(F.data.startswith(_PICK_PREFIX))
async def on_pick_type(callback: CallbackQuery, bot: Bot) -> None:
    """Provision the pending managed bot with the owner's chosen type."""
    if callback.from_user is None:
        with suppress(TelegramBadRequest):
            await callback.answer("Something went wrong — please try again.")
        return

    # The router filter guarantees a non-empty data, but the model types it
    # optional — normalize for the type checker.
    raw = callback.data or ""
    choice = _TYPE_CHOICES.get(raw.removeprefix(_PICK_PREFIX))
    if choice is None:
        with suppress(TelegramBadRequest):
            await callback.answer("Unknown bot type.")
        return

    _, bot_type = choice
    token = _PENDING.pop(callback.from_user.id, None)
    if token is None:
        with suppress(TelegramBadRequest):
            await callback.answer("That request expired — tap “Create a Managed Bot” again.")
        return

    with suppress(TelegramBadRequest):
        await callback.answer()
    try:
        bot_row = await provision_managed_bot(
            token=token,
            owner_telegram_id=callback.from_user.id,
            owner_username=callback.from_user.username,
            owner_first_name=callback.from_user.first_name,
            bot_type=bot_type,
        )
    except Exception:
        logger.exception("provision_managed_bot failed for owner=%s", callback.from_user.id)
        # Provisioning is idempotent on token — put it back so the owner can
        # simply re-pick instead of recreating the bot from scratch.
        _PENDING[callback.from_user.id] = token
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                "❌ Something went wrong while wiring up your bot. "
                "Tap your chosen type again to retry — or create it anew "
                "if it keeps failing."
            ),
        )
        return

    display = f"@{bot_row.username}" if bot_row.username else "your bot"
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"✅ {display} is live! Try sending it /start.",
        reply_markup=_settings_keyboard(bot_row.id),
    )


def _settings_keyboard(bot_id: int) -> InlineKeyboardMarkup:
    """Inline button that opens the BotFather-style settings dashboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Open Settings",
                    callback_data=f"{_SETTINGS_PREFIX}{bot_id}",
                )
            ]
        ]
    )


def _bots_keyboard(bots: list[BotModel]) -> InlineKeyboardMarkup:
    """One '⚙️ Open Settings' button per owned bot (My Bots list)."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"⚙️ {f'@{bot.username}' if bot.username else f'Bot #{bot.id}'}",
                callback_data=f"{_SETTINGS_PREFIX}{bot.id}",
            )
        ]
        for bot in bots
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_my_bots(bot: Bot, owner_id: int) -> None:
    """Send the owner's bot list with a settings button per bot."""
    bots = await list_bots_for_owner(owner_telegram_id=owner_id)
    if not bots:
        await bot.send_message(
            chat_id=owner_id,
            text=(
                "🤖 You don't have any bots yet — tap "
                "“➕ Create a Managed Bot” to make your first one."
            ),
        )
        return
    await bot.send_message(
        chat_id=owner_id,
        text=(f"Your bots ({len(bots)}):\n\nTap a bot to open its ⚙️ settings."),
        reply_markup=_bots_keyboard(bots),
    )


@main_router.message(Command("mybots"))
@main_router.message(F.text.in_(["My Bots", "🤖 My Bots"]))
async def on_my_bots(message: Message, bot: Bot) -> None:
    """List the owner's bots (works for bots created before this feature too)."""
    if message.from_user is None:
        return
    await _show_my_bots(bot, message.from_user.id)


@main_router.callback_query(F.data.startswith(_SETTINGS_PREFIX))
async def on_open_settings(callback: CallbackQuery, bot: Bot) -> None:
    """Issue a short-lived dashboard link for one of the owner's bots."""
    if callback.from_user is None:
        with suppress(TelegramBadRequest):
            await callback.answer("Something went wrong — please try again.")
        return

    raw = callback.data or ""
    try:
        bot_id = int(raw.removeprefix(_SETTINGS_PREFIX))
    except ValueError:
        with suppress(TelegramBadRequest):
            await callback.answer("Unknown bot.")
        return

    token = await create_dashboard_token_for_owner(
        bot_id=bot_id, owner_telegram_id=callback.from_user.id
    )
    if token is None:
        with suppress(TelegramBadRequest):
            await callback.answer("Bot not found.")
        return

    link = f"{settings.webhook_base_url}/dashboard/?t={token}&bid={bot_id}"
    with suppress(TelegramBadRequest):
        await callback.answer()
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            f"⚙️ Here are the settings for your bot:\n{link}\n\n"
            "The link expires in 15 minutes — ask for a new one any time."
        ),
        disable_web_page_preview=True,
    )


@main_router.message(F.managed_bot_created)
async def on_managed_bot_created_message(message: Message) -> None:
    """Service message confirming the bot creation was initiated."""
    # This fires before the ManagedBotUpdated update arrives.
    # No action needed — provisioning happens in on_managed_bot_updated.
    logger.info("ManagedBotCreated service message received")

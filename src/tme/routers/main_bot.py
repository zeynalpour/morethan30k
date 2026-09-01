"""The Main / Controller bot router.

This is the bot users talk to in order to create and manage their own tenant
bots. It runs on its own dispatcher (``main_dp``) — kept separate from the
tenant dispatcher so management commands can never leak into cloned bots.

Note on ``managed_bot``: because that update type is not represented in
aiogram's typed ``Update`` model, it is dispatched from the webhook gateway
straight to :func:`tme.services.managed_bots.handle_managed_bot` rather
than through a decorator here. See that module's docstring for the rationale.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.methods import GetManagedBotToken
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    KeyboardButtonRequestManagedBot,
    ManagedBotUpdated,
    Message,
    ReplyKeyboardMarkup,
)

from tme.core.logging import get_logger
from tme.services.managed_bots import provision_managed_bot

logger = get_logger(__name__)

main_router = Router(name="main_controller")

_CREATE_BOT = "create_bot"


def _create_bot_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard that triggers Telegram's managed bot creation dialog."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="➕ Create a Managed Bot",
                    request_managed_bot=KeyboardButtonRequestManagedBot(),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
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

    In a live deployment this is where you trigger Telegram's managed-bot
    authorisation for the user. Once they authorise, Telegram sends a
    ``managed_bot`` update to this Main Bot's webhook, which the gateway
    routes to the provisioning service — no further action is needed here.
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
    managed_bot_id = event.bot.id
    username = event.bot.username

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
        return

    await provision_managed_bot(
        token=token,
        owner_telegram_id=owner_id,
        owner_username=event.user.username,
        owner_first_name=event.user.first_name,
    )

    await bot.send_message(
        chat_id=owner_id,
        text=f"✅ Your bot @{username} is live! Try sending it /start.",
    )


@main_router.message(F.managed_bot_created)
async def on_managed_bot_created_message(message: Message) -> None:
    """Service message confirming the bot creation was initiated."""
    # This fires before the ManagedBotUpdated update arrives.
    # No action needed — provisioning happens in on_managed_bot_updated.
    logger.info("ManagedBotCreated service message received")

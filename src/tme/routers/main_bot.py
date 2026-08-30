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

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from tme.core.logging import get_logger

logger = get_logger(__name__)

main_router = Router(name="main_controller")

_CREATE_BOT = "create_bot"


def _controller_menu() -> InlineKeyboardMarkup:
    """Inline keyboard for the controller's home screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Create New Bot", callback_data=_CREATE_BOT)],
        ]
    )


@main_router.message(CommandStart())
async def controller_start(message: Message) -> None:
    """Greet the user and offer the 'Create New Bot' action."""
    name = message.from_user.first_name if message.from_user else "there"
    await message.answer(
        f"👋 Hi {name}! Welcome to the <b>TME</b> bot platform.\n\n"
        "Here you can spin up your own custom Telegram bot in seconds. "
        "Tap the button below to get started.",
        reply_markup=_controller_menu(),
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

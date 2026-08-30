"""Managed-bot provisioning service.

This module implements spec §2 / §4-Step-2: when a user authorises the Main Bot
to create a bot on their behalf, we obtain the new bot's token, persist it, and
wire it into the platform (cache + webhook) so it is immediately live.

--------------------------------------------------------------------------------
⚠️  API CAVEAT — READ THIS
--------------------------------------------------------------------------------
The spec references a ``ManagedBotUpdated`` update and a ``getManagedBotToken``
Bot API method. These are **not part of the standard, publicly documented
Telegram Bot API / aiogram 3.x** at the time of writing. They are implemented
here exactly as the spec describes, but behind a thin, clearly-marked seam:

* :class:`GetManagedBotToken` is a custom :class:`~aiogram.methods.base.TelegramMethod`
  so it flows through aiogram's normal request pipeline. If/when Telegram ships
  the real method (possibly with different field names), only this class needs
  to change.
* :func:`handle_managed_bot` consumes the **raw update dict**, because
  aiogram's typed ``Update`` model will not contain an unknown field.

If your Bot API build does not expose ``getManagedBotToken``, the call will
raise :class:`~aiogram.exceptions.TelegramBadRequest`; we catch and log it so a
single bad event never takes the gateway down.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.methods.base import TelegramMethod
from sqlalchemy import select

from tme.config import settings
from tme.core.bot_registry import get_tenant_bot
from tme.core.cache import set_bot_config
from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, BotConfig, User
from tme.schemas.bot_config import BotConfigSchema

logger = get_logger(__name__)


class GetManagedBotToken(TelegramMethod[str]):
    """Custom Bot API method: fetch the token of a bot the user just authorised.

    .. warning::
       Unverified against the public Bot API — see the module docstring. The
       field name below (``managed_bot_user_id``) is a best-effort guess; adjust
       it to match the real method signature once confirmed.
    """

    __returning__ = str
    __api_method__ = "getManagedBotToken"

    #: Telegram user id of the managed bot whose token we are requesting.
    managed_bot_user_id: int


async def _upsert_owner(
    session: Any,
    *,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> User:
    """Return the owning :class:`User`, creating it on first sight."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        session.add(user)
        await session.flush()  # assign user.id for the FK below
    return user


async def provision_managed_bot(
    *,
    token: str,
    owner_telegram_id: int,
    owner_username: str | None = None,
    owner_first_name: str | None = None,
) -> BotModel:
    """Persist a newly-created tenant bot and make it live.

    Steps: fetch identity via ``getMe`` → upsert owner + Bot + default config in
    Postgres → prime the Redis cache → register the per-bot webhook. Idempotent
    on ``token`` (re-provisioning an existing token updates it in place).
    """
    tenant_bot = get_tenant_bot(token)

    # Identify the bot (best-effort; failure here is non-fatal for persistence).
    username: str | None = None
    title: str | None = None
    try:
        me = await tenant_bot.get_me()
        username, title = me.username, me.full_name
    except TelegramAPIError as exc:  # pragma: no cover - network dependent
        logger.warning("getMe failed for new bot …%s: %s", token[-6:], exc)

    telegram_bot_id = int(token.split(":", 1)[0])
    default_config = BotConfigSchema.default()

    async with session_scope() as session:
        owner = await _upsert_owner(
            session,
            telegram_id=owner_telegram_id,
            username=owner_username,
            first_name=owner_first_name,
        )

        existing = await session.execute(select(BotModel).where(BotModel.token == token))
        bot_row = existing.scalar_one_or_none()
        if bot_row is None:
            bot_row = BotModel(
                token=token,
                telegram_bot_id=telegram_bot_id,
                username=username,
                title=title,
                owner_id=owner.id,
                is_active=True,
            )
            bot_row.config = BotConfig(flow=default_config.model_dump())
            session.add(bot_row)
        else:
            bot_row.username, bot_row.title, bot_row.is_active = username, title, True
        await session.flush()

    # Prime the cache so the very first user update is already a hit.
    await set_bot_config(token, default_config)

    # Register the webhook so Telegram routes this bot's updates to us.
    await register_webhook(token)

    logger.info("Provisioned managed bot @%s (tg=%s)", username, telegram_bot_id)
    return bot_row


async def register_webhook(token: str) -> None:
    """Point a bot's Telegram webhook at our universal gateway endpoint."""
    tenant_bot = get_tenant_bot(token)
    await tenant_bot.set_webhook(
        url=settings.webhook_url_for(token),
        secret_token=settings.webhook_secret.get_secret_value(),
        drop_pending_updates=True,
    )
    async with session_scope() as session:
        result = await session.execute(select(BotModel).where(BotModel.token == token))
        if (row := result.scalar_one_or_none()) is not None:
            row.webhook_registered = True


async def handle_managed_bot(raw_update: dict[str, Any], main_bot: Bot) -> None:
    """Handle a raw ``managed_bot`` update from the Main Bot's webhook.

    Extracts the managed bot's id + the authorising owner, calls
    :class:`GetManagedBotToken` in the background, then provisions the bot.

    ``raw_update`` is the *entire* update dict; we read the ``managed_bot``
    sub-object defensively because its exact shape is not yet documented.
    """
    payload = raw_update.get("managed_bot") or {}

    # Defensive extraction — accept a few plausible field spellings.
    managed_bot_user_id = (
        payload.get("bot_user_id") or payload.get("managed_bot_user_id") or payload.get("user_id")
    )
    owner = payload.get("owner") or payload.get("from") or {}
    owner_id = owner.get("id") if isinstance(owner, dict) else None

    if not managed_bot_user_id or not owner_id:
        logger.warning("managed_bot missing ids; payload keys=%s", list(payload))
        return

    try:
        token = await main_bot(GetManagedBotToken(managed_bot_user_id=int(managed_bot_user_id)))
    except TelegramAPIError as exc:
        logger.error("getManagedBotToken failed for bot_user_id=%s: %s", managed_bot_user_id, exc)
        return

    await provision_managed_bot(
        token=token,
        owner_telegram_id=int(owner_id),
        owner_username=owner.get("username") if isinstance(owner, dict) else None,
        owner_first_name=owner.get("first_name") if isinstance(owner, dict) else None,
    )

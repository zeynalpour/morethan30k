"""Managed-bot provisioning service.

When a user makes the Main Bot create a bot on their behalf, Telegram delivers a
``managed_bot`` update to the controller, which is handled by the typed
``@main_router.managed_bot()`` handler and by the native
``aiogram.methods.GetManagedBotToken``. This module contains the persistence
and wiring steps that turn that token into a live tenant: persist the bot +
default config, prime the Redis cache, and register the per-bot webhook.

This follows the official Bot API (Managed Bots were added in Bot API 9.6 on
April 3, 2026) and aiogram 3.x native support.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from tme.config import settings
from tme.core.bot_registry import get_tenant_bot
from tme.core.cache import set_bot_config
from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, BotConfig, User
from tme.schemas.bot_config import BotConfigSchema

logger = get_logger(__name__)

# Webhook registration tuning — mirrors the main-bot retry policy in main.py.
# A freshly created managed bot's token is subject to Telegram-side
# eventual consistency, so the first setWebhook call can fail or not persist.
_WEBHOOK_REGISTER_ATTEMPTS = 5
_WEBHOOK_RETRY_BASE_DELAY = 2.0  # seconds; capped exponential backoff.
_WEBHOOK_RETRY_MAX_DELAY = 30.0


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

    # Register the webhook so Telegram routes this bot's updates to us. The
    # result is surfaced in the logs but does not fail provisioning — the bot is
    # persisted and cached even if Telegram-side eventual consistency delays the
    # webhook; callers can retry later.
    webhook_ok = await register_webhook(token)
    if not webhook_ok:
        logger.warning("Webhook not yet registered for managed bot …%s", token[-6:])

    logger.info("Provisioned managed bot @%s (tg=%s)", username, telegram_bot_id)
    return bot_row


async def register_webhook(token: str) -> bool:
    """Point a bot's Telegram webhook at our universal gateway endpoint.

    Retries with capped exponential backoff (a fresh managed bot's token can be
    subject to Telegram-side eventual consistency, so the first attempt may fail
    or not persist), then **verifies** via ``getWebhookInfo`` that the URL stuck.
    Returns ``True`` on success. On final failure returns ``False`` and leaves
    ``webhook_registered`` untouched so callers can surface a degraded state.
    """
    target_url = settings.webhook_url_for(token)
    tenant_bot = get_tenant_bot(token)

    for attempt in range(1, _WEBHOOK_REGISTER_ATTEMPTS + 1):
        try:
            await tenant_bot.set_webhook(
                url=target_url,
                secret_token=settings.webhook_secret.get_secret_value(),
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query", "managed_bot"],
            )
        except TelegramAPIError as exc:
            logger.warning(
                "setWebhook attempt %d/%d failed for …%s: %s",
                attempt,
                _WEBHOOK_REGISTER_ATTEMPTS,
                token[-6:],
                exc,
            )
        else:
            # Verify the webhook actually registered (catches the case where
            # Telegram accepted the call but hasn't persisted it yet).
            try:
                info = await tenant_bot.get_webhook_info()
                if info.url == target_url:
                    logger.info("Registered webhook for …%s: %s", token[-6:], target_url)
                    async with session_scope() as session:
                        result = await session.execute(
                            select(BotModel).where(BotModel.token == token)
                        )
                        if (row := result.scalar_one_or_none()) is not None:
                            row.webhook_registered = True
                    return True
                logger.warning(
                    "setWebhook attempt %d/%d returned OK but getWebhookInfo url "
                    "mismatch for …%s (got %r, want %r)%s",
                    attempt,
                    _WEBHOOK_REGISTER_ATTEMPTS,
                    token[-6:],
                    info.url,
                    target_url,
                    (f"; last error: {info.last_error_message}" if info.last_error_message else ""),
                )
            except TelegramAPIError as exc:
                logger.warning(
                    "getWebhookInfo failed for …%s on attempt %d/%d: %s",
                    token[-6:],
                    attempt,
                    _WEBHOOK_REGISTER_ATTEMPTS,
                    exc,
                )

        if attempt < _WEBHOOK_REGISTER_ATTEMPTS:
            delay = min(
                _WEBHOOK_RETRY_BASE_DELAY * 2 ** (attempt - 1),
                _WEBHOOK_RETRY_MAX_DELAY,
            )
            await asyncio.sleep(delay)

    logger.error(
        "Giving up registering webhook for …%s after %d attempts; target %s "
        "(bot will not receive updates until this is resolved)",
        token[-6:],
        _WEBHOOK_REGISTER_ATTEMPTS,
        target_url,
    )
    return False

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
from tme.core.cache import invalidate_bot_config, set_bot_config
from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, BotConfig, BotType, User
from tme.schemas.bot_config import (
    BotConfigSchema,
    BotConfigUnion,
    EchoBotConfig,
    HelloBotConfig,
    parse_bot_config,
)
from tme.services.vault import _master_key, store_bot_token, token_hash
from tme.templates import TemplateSpec, clone_flow

logger = get_logger(__name__)

# Webhook registration tuning — mirrors the main-bot retry policy in main.py.
# A freshly created managed bot's token is subject to Telegram-side
# eventual consistency, so the first setWebhook call can fail or not persist.
_WEBHOOK_REGISTER_ATTEMPTS = 5
_WEBHOOK_RETRY_BASE_DELAY = 2.0  # seconds; capped exponential backoff.
_WEBHOOK_RETRY_MAX_DELAY = 30.0


def _default_config_for(bot_type: BotType) -> BotConfigUnion:
    """Return a starter config appropriate to a bot's type.

    Generic bots get the familiar rich starter. Hello/Echo add their type
    defaults so a freshly-provisioned bot already behaves like its kind before
    the management UI tunes it.
    """
    if bot_type is BotType.HELLO:
        return HelloBotConfig(greeting="Hello there! 👋")
    if bot_type is BotType.ECHO:
        return EchoBotConfig(echo_prefix="🔁 ")
    return BotConfigSchema.default()


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


def _vault_status() -> bool:
    """True if the vault is configured (master key present)."""
    try:
        _master_key()
    except ValueError:
        return False
    return True


async def provision_managed_bot(
    *,
    token: str,
    owner_telegram_id: int,
    owner_username: str | None = None,
    owner_first_name: str | None = None,
    bot_type: BotType = BotType.GENERIC,
    seed: BotConfigUnion | None = None,
) -> BotModel:
    """Persist a newly-created tenant bot and make it live.

    Steps: fetch identity via ``getMe`` → upsert owner + tenant + default config
    in Postgres → prime the Redis cache → register the per-bot webhook.
    Idempotent on ``token`` (re-provisioning an existing token updates it in
    place). ``bot_type`` selects the config variant; the default is a generic
    tenant bot.

    S2.2: ``seed`` is the template's flow (``TemplateSpec.seed`` via
    :func:`tme.templates.get_template`). When provided it **replaces** the
    bare per-type default entirely — and the row's ``bot_type`` is taken from
    the seed's own discriminator (never from ``bot_type``), keeping the
    row/flow invariant a template cannot violate. ``seed=None`` (API/legacy
    callers) keeps today's ``_default_config_for(bot_type)`` behaviour;
    ``_default_config_for`` stays until S2.3 collapses the two sources.
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
    # The seed carries its own bot_type discriminator — the row must agree
    # with the flow it persists (same invariant the registry stamps at source).
    effective_type = seed.bot_type if seed is not None else bot_type
    default_config = seed if seed is not None else _default_config_for(bot_type)

    async with session_scope() as session:
        owner = await _upsert_owner(
            session,
            telegram_id=owner_telegram_id,
            username=owner_username,
            first_name=owner_first_name,
        )

        existing = await session.execute(select(BotModel).where(BotModel.token == token))
        bot_row = existing.scalar_one_or_none()
        vault_on = _vault_status()
        if not vault_on:
            logger.warning(
                "VAULT_MASTER_KEY not set — provisioning without vaulting the "
                "token (plaintext column remains the source of truth)"
            )
        if bot_row is None:
            bot_row = BotModel(
                token=token,
                token_hash=token_hash(token),
                telegram_bot_id=telegram_bot_id,
                username=username,
                title=title,
                owner_id=owner.id,
                is_active=True,
                bot_type=effective_type,
            )
            bot_row.config = BotConfig(flow=default_config.model_dump())
            session.add(bot_row)
            await session.flush()  # assign bot_row.id for the vault FK
            if vault_on:
                await store_bot_token(session, bot_id=bot_row.id, token=token)
        else:
            bot_row.username, bot_row.title, bot_row.is_active = username, title, True
            bot_row.token_hash = token_hash(token)
            if vault_on:
                await store_bot_token(session, bot_id=bot_row.id, token=token)
        await session.flush()

    # Prime the cache with EXACTLY what was persisted. A fresh per-type default
    # would diverge from an existing row (whose config may be user-customized,
    # or of a different type than this call's `bot_type`) and serve stale
    # behaviour until the cache TTL expired. For a legacy row with no
    # BotConfig at all, fall back to the default for the row's OWN type.
    persisted_flow = (
        bot_row.config.flow
        if bot_row.config is not None
        else _default_config_for(bot_row.bot_type).model_dump()
    )
    await set_bot_config(token, parse_bot_config(persisted_flow))

    # Register the webhook so Telegram routes this bot's updates to us. The
    # result is surfaced in the logs but does not fail provisioning — the bot is
    # persisted and cached even if Telegram-side eventual consistency delays the
    # webhook; callers can retry later.
    webhook_ok = await register_webhook(token)
    if not webhook_ok:
        logger.warning("Webhook not yet registered for managed bot …%s", token[-6:])

    logger.info("Provisioned managed bot @%s (tg=%s)", username, telegram_bot_id)
    return bot_row


async def reclone_bot(
    bot_row: BotModel,
    spec: TemplateSpec,
    *,
    preserve: str | list[str] | None = None,
) -> BotModel:
    """Apply ``spec``'s seed to an EXISTING bot's base flow (S2.3 re-clone).

    The explicit owner action behind "Reset to template": replaces the base
    copy wholesale with the seed's flow, carries the whitelisted owner keys
    (``translations`` + ``single_language`` by default), updates the row's
    provenance stamp to the applied version, and keeps the row/flow
    discriminator invariant (``bot_type`` follows the seed — pre-Phase-2 and
    scratch bots can therefore ADOPT a template through this same path).

    Owns its transaction, mirroring :func:`provision_managed_bot`: the row is
    re-attached (``merge``) and flushed inside ``session_scope``, then the
    Redis key is invalidated AFTER the session closed so the next request
    read-throughs Postgres — the single source of truth. ``bot_row`` may be
    detached (the API route loads it owner-scoped, closes the session and
    delegates here) or a brand-new row; the rebuilt flow is revalidated
    through :class:`~tme.schemas.bot_config.BotConfigUnion` BEFORE anything
    is persisted, so a seed the engine would reject never reaches the DB and
    a rejected call never invalidates a live cache entry.

    Raises:
        ValueError: ``preserve`` names a key outside the closed whitelist, or
            the rebuilt flow's discriminator disagrees with the template (a
            registry bug — S2.1's import-time loop pins the other way).

    Idempotent: re-cloning the same version twice persists the same flow.
    """
    old_flow = dict(bot_row.config.flow) if bot_row.config is not None else {}
    new_flow = clone_flow(spec, old_flow, preserve)

    # Revalidate the rebuilt flow through the same union the runtime parses
    # with — provenance stamp and preserved keys ride `extra="allow"`/typed
    # fields, so a broken combination fails HERE, never at a live update.
    parsed = parse_bot_config(new_flow)
    if parsed.bot_type != spec.bot_type:  # pragma: no cover - registry pins this
        raise ValueError(
            f"rebuilt flow bot_type {parsed.bot_type!r} does not match template {spec.id!r}"
        )

    if bot_row.config is None:
        bot_row.config = BotConfig(flow=new_flow)
    else:
        bot_row.config.flow = new_flow
    # The row/discriminator invariant every other write path enforces —
    # adoption (scratch hello bot → hello_world template) flips the type
    # in the same transaction.
    bot_row.bot_type = spec.bot_type

    async with session_scope() as session:
        await session.merge(bot_row)
        await session.flush()

    # Invalidate AFTER the commit: the next request re-reads the row from
    # Postgres and re-validates it (same path as disable/enable, type switch
    # and config edits — never a direct `set_bot_config`).
    await invalidate_bot_config(bot_row.token)

    logger.info(
        "Re-cloned bot id=%s to template %s v%s (preserve=%s)",
        getattr(bot_row, "id", "?"),
        spec.id,
        spec.version,
        preserve,
    )
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

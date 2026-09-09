"""Settings API for the BotFather-style dashboard (S0.4).

Every endpoint requires a valid dashboard bearer token (issued by the Main
Bot, see :mod:`tme.services.dashboard`) and is scoped to the token's owner:
a user can list, read, and edit **only their own bots**. The bot token itself
is never serialized; config writes go through the same validation as the
runtime path and invalidate the Redis cache so edits go live immediately.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.core.cache import invalidate_bot_config
from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, BotType, User
from tme.schemas.bot_config import BotConfigUnion
from tme.services.auth import validate_telegram_init_data
from tme.services.managed_bots import _default_config_for

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class BotSummary(BaseModel):
    """A bot as seen by its owner — never includes the token."""

    id: int
    username: str | None
    title: str | None
    bot_type: str
    is_active: bool
    webhook_registered: bool
    created_at: datetime


class ConfigUpdate(BaseModel):
    """Body of ``PATCH /api/bots/{id}/config`` — the full flow dict."""

    flow: dict


class BotUpdate(BaseModel):
    """Body of ``PATCH /api/bots/{id}`` — management-plane knobs."""

    is_active: bool | None = None
    bot_type: BotType | None = None


# --------------------------------------------------------------------------- #
# Auth + shared helpers
# --------------------------------------------------------------------------- #
async def _require_auth(
    x_telegram_init_data: str | None = Header(default=None),
) -> int:
    """Resolve the caller's telegram id from the Mini App's ``initData``.

    Telegram signs ``initData`` with the bot token, so a valid signature IS
    the authentication — every endpoint then scopes to this owner id.
    """
    telegram_id = validate_telegram_init_data(x_telegram_init_data)
    if telegram_id is None:
        logger.warning(
            "Rejected dashboard API request: missing or invalid initData "
            "(mini app opened outside Telegram, or domain not registered in BotFather)"
        )
        raise HTTPException(status_code=401, detail="Missing or invalid Telegram auth")
    return telegram_id


async def _load_owned_bot(
    session: AsyncSession, bot_id: int, owner_telegram_id: int
) -> BotModel | None:
    """Fetch a bot iff it belongs to ``owner_telegram_id`` (else ``None``)."""
    result = await session.execute(
        select(BotModel)
        .join(BotModel.owner)
        .where(BotModel.id == bot_id, User.telegram_id == owner_telegram_id)
    )
    return result.scalar_one_or_none()


def _summarize(bot: BotModel) -> BotSummary:
    return BotSummary(
        id=bot.id,
        username=bot.username,
        title=bot.title,
        bot_type=bot.bot_type.value if hasattr(bot.bot_type, "value") else str(bot.bot_type),
        is_active=bot.is_active,
        webhook_registered=bot.webhook_registered,
        created_at=bot.created_at,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/bots", response_model=list[BotSummary])
async def list_bots(auth: int = Depends(_require_auth)) -> list[BotSummary]:
    """All bots owned by the authenticated user (newest first)."""
    async with session_scope() as session:
        result = await session.execute(
            select(BotModel)
            .join(BotModel.owner)
            .where(User.telegram_id == auth)
            .order_by(BotModel.created_at.desc())
        )
        return [_summarize(bot) for bot in result.scalars()]


@router.get("/bots/{bot_id}", response_model=BotSummary)
async def get_bot(bot_id: int, auth: int = Depends(_require_auth)) -> BotSummary:
    """One owned bot (404 for bots that don't exist or aren't yours)."""
    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return _summarize(bot)


@router.get("/bots/{bot_id}/config")
async def get_bot_config(bot_id: int, auth: int = Depends(_require_auth)) -> dict:
    """The bot's raw flow JSON (validated on write, served as stored here)."""
    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
    if bot is None or bot.config is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot.config.flow


@router.patch("/bots/{bot_id}/config")
async def update_bot_config(
    bot_id: int,
    payload: ConfigUpdate,
    auth: int = Depends(_require_auth),
) -> dict:
    """Replace the bot's flow (strictly validated), then invalidate the cache.

    The ``bot_type`` column is kept in sync with the flow's discriminator so
    the DB row and the executed config can never disagree.
    """
    try:
        parsed: BotConfigUnion = TypeAdapter(BotConfigUnion).validate_python(payload.flow)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid flow", "detail": exc.errors(include_url=False)},
        ) from exc

    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
        if bot is None or bot.config is None:
            raise HTTPException(status_code=404, detail="Bot not found")
        bot.config.flow = parsed.model_dump()
        bot.bot_type = parsed.bot_type
        token = bot.token  # captured before the session closes

    await invalidate_bot_config(token)
    logger.info("Updated config for bot id=%s (owner tg=%s)", bot_id, auth)
    return bot.config.flow


@router.patch("/bots/{bot_id}", response_model=BotSummary)
async def update_bot(
    bot_id: int,
    payload: BotUpdate,
    auth: int = Depends(_require_auth),
) -> BotSummary:
    """Toggle a bot on/off or switch its type (resets flow to that type's default).

    Disabling is a hard stop: the runtime only resolves ACTIVE bots
    (:func:`tme.core.cache.get_bot_config`), so the cache is dropped to make
    the change immediate instead of waiting for the TTL. Re-enabling reloads
    from Postgres. Switching ``bot_type`` re-applies the new type's default
    config — the same reset provisioning does.
    """
    if payload.is_active is None and payload.bot_type is None:
        raise HTTPException(status_code=422, detail="Nothing to update")

    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
        if bot is None:
            raise HTTPException(status_code=404, detail="Bot not found")

        if payload.bot_type is not None and payload.bot_type != bot.bot_type:
            bot.bot_type = payload.bot_type
            if bot.config is not None:
                bot.config.flow = _default_config_for(payload.bot_type).model_dump()
        if payload.is_active is not None:
            bot.is_active = payload.is_active

        summary = _summarize(bot)
        token = bot.token  # captured before the session closes

    await invalidate_bot_config(token)
    logger.info(
        "Updated bot id=%s (owner tg=%s): active=%s type=%s",
        bot_id,
        auth,
        summary.is_active,
        summary.bot_type,
    )
    return summary

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
from tme.services.managed_bots import _default_config_for, reclone_bot
from tme.templates import (
    get_template,
    list_templates,
    stamp_of,
    template_updates,
)

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


class TemplateSummary(BaseModel):
    """One registry template as the dashboard picker sees it (no seed data)."""

    id: str
    version: int
    bot_type: str
    display_name: str
    description: str


class TemplateProvenance(BaseModel):
    """A bot's template lineage + whether the registry has moved on (S2.3).

    ``current`` is the stamp pinned in the flow (``null`` for scratch and
    pre-Phase-2 bots — they can still adopt via ``POST .../reclone``).
    ``latest_version`` is the registry's newest version of that same id
    (``null`` when there is no current template). ``update_available`` is a
    pure badge: bumping a template never mutates live bots — every re-clone
    is an explicit, confirmed owner action.
    """

    current: dict | None
    latest_version: int | None
    update_available: bool


class RecloneRequest(BaseModel):
    """Body of ``POST /api/bots/{id}/reclone`` — the explicit owner reset.

    ``template_id``/``version`` select the seed (version ``None`` = latest);
    ``preserve`` whitelists which owner keys survive the reset
    (``translations`` + ``single_language`` — default BOTH, per the Phase 2
    invariant that the i18n layer is the owner's, never the template's).
    """

    template_id: str
    version: int | None = None
    preserve: list[str] | None = None


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
                # A hand reset clears the lineage: the bare per-type default
                # is not the stamped seed the owner cloned, so the dashboard
                # must stop advertising "update available" for a flow the
                # template no longer describes (Architect's S2.3 gotcha —
                # _default_config_for emits NO stamp, so this is a wipe by
                # construction, not a conditional one).
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


# --------------------------------------------------------------------------- #
# Templates (S2.3 — versioned templates + re-clone into existing bots)
# --------------------------------------------------------------------------- #
@router.get("/templates", response_model=list[TemplateSummary])
async def list_available_templates(auth: int = Depends(_require_auth)) -> list[TemplateSummary]:
    """Every registry template's latest version, in registration order.

    Serialized straight from :func:`tme.templates.list_templates` — the
    registry stays the single source; no duplicated list in the frontend.
    initData-auth'd like every ``/api`` route (owners only; templates are
    not public data).
    """
    return [
        TemplateSummary(
            id=spec.id,
            version=spec.version,
            bot_type=spec.bot_type.value,
            display_name=spec.display_name,
            description=spec.description,
        )
        for spec in list_templates()
    ]


@router.get("/bots/{bot_id}/template", response_model=TemplateProvenance)
async def get_bot_template(bot_id: int, auth: int = Depends(_require_auth)) -> TemplateProvenance:
    """The bot's template provenance + the "update available" badge.

    A pure read of the flow's stamp against the registry. Scratch and
    pre-Phase-2 bots answer ``current: null`` — they can still adopt any
    template through ``POST /api/bots/{id}/reclone``.
    """
    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
    if bot is None or bot.config is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    flow = bot.config.flow
    current = stamp_of(flow) if isinstance(flow, dict) else None
    if current is not None:
        try:
            latest_version: int | None = get_template(current["id"]).version
        except KeyError:
            latest_version = None  # template since deleted from the registry
    else:
        latest_version = None
    update = template_updates(flow) is not None
    return TemplateProvenance(
        current=current,
        latest_version=latest_version,
        update_available=update,
    )


@router.post("/bots/{bot_id}/reclone", response_model=BotSummary)
async def reclone_from_template(
    bot_id: int,
    payload: RecloneRequest,
    auth: int = Depends(_require_auth),
) -> BotSummary:
    """Reset the bot's base flow to a template seed — the explicit owner action.

    No silent auto-updates: this is the ONLY path a template version enters
    an existing bot. The seed replaces the base copy wholesale
    (validated through the union first); ``translations`` and
    ``single_language`` carry over from the old flow (whitelisted
    ``preserve``); the stamp moves to the applied version. Cross-type
    re-clones are refused (422) — Phase 2 keeps re-clone same-``bot_type``
    only, the Architect's simpler option; adopting a template of the bot's
    OWN type is the adoption path for scratch/legacy bots. The write and the
    cache invalidation belong to :func:`tme.services.managed_bots.reclone_bot`
    — this route only authenticates, scopes, guards and serializes.
    """
    try:
        spec = get_template(payload.template_id, version=payload.version)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown template", "template_id": payload.template_id},
        ) from None

    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
        if bot is None:
            raise HTTPException(status_code=404, detail="Bot not found")
        if spec.bot_type != bot.bot_type:
            # Same-bot_type only in Phase 2 (Architect's recommendation):
            # re-clone is a reset of THIS kind of bot, not a type switch.
            # The type switch keeps its own path (PATCH /api/bots/{id}).
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "template bot_type mismatch",
                    "template_bot_type": spec.bot_type.value,
                    "bot_bot_type": (
                        bot.bot_type.value if hasattr(bot.bot_type, "value") else str(bot.bot_type)
                    ),
                },
            )

    # The service owns the write AND the cache invalidation (same contract as
    # provisioning): the rebuilt flow is persisted in its own session, then
    # the Redis key is dropped so the next request read-throughs Postgres.
    # Ordering matters: a rejected whitelist never touches the row or the
    # live cache entry.
    try:
        bot = await reclone_bot(bot, spec, preserve=payload.preserve)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc

    summary = _summarize(bot)
    logger.info(
        "Re-cloned bot id=%s (owner tg=%s) to template %s v%s (type=%s)",
        bot_id,
        auth,
        spec.id,
        spec.version,
        summary.bot_type,
    )
    return summary

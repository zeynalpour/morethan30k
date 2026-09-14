"""Settings API for the BotFather-style dashboard (S0.4).

Every endpoint requires a valid dashboard bearer token (issued by the Main
Bot, see :mod:`tme.services.dashboard`) and is scoped to the token's owner:
a user can list, read, and edit **only their own bots**. The bot token itself
is never serialized; config writes go through the same validation as the
runtime path and invalidate the Redis cache so edits go live immediately.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.core.cache import invalidate_bot_config, invalidate_bot_config_hash
from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, BotType, User
from tme.schemas.bot_config import BotConfigUnion
from tme.services.auth import validate_telegram_init_data
from tme.services.bot_health import (
    bot_is_alive,
    cached_bot_liveness,
    forget_bot_liveness,
    probe_bots,
)
from tme.services.managed_bots import _default_config_for, reclone_bot
from tme.services.vault import (
    delete_bot_token,
    resolve_bot_token,
    resolve_bot_tokens,
)
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
    #: ``active`` (serving), ``paused`` (owner switched it off), or
    #: ``archived`` (killed in BotFather — token revoked, detected by probe).
    state: str


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


def _state_for(is_active: bool, alive: bool | None) -> str:
    """Dashboard state for a bot.

    ``paused``   — the owner switched it off (not probed).
    ``archived`` — Telegram rejected the token: deleted in BotFather.
    ``active``   — serving (or liveness not checked on this path).
    """
    if not is_active:
        return "paused"
    if alive is False:
        return "archived"
    return "active"


def _summarize(bot: BotModel, *, alive: bool | None = None) -> BotSummary:
    return BotSummary(
        id=bot.id,
        username=bot.username,
        title=bot.title,
        bot_type=bot.bot_type.value if hasattr(bot.bot_type, "value") else str(bot.bot_type),
        is_active=bot.is_active,
        webhook_registered=bot.webhook_registered,
        created_at=bot.created_at,
        state=_state_for(bot.is_active, alive),
    )


async def _reject_if_archived(bot: BotModel) -> None:
    """Refuse writes to a bot Telegram has already rejected.

    An archived bot was deleted in BotFather: it has no live token, so an edit
    is either silently useless or resurrects a bot the owner removed. The row
    itself is kept (soft delete) so history stays available later.

    Uses the **cached** verdict only — a write path never calls the Bot API
    (a probe per save would add latency and could block a live bot when
    Telegram is unreachable). No cached verdict means "allow"; the dashboard
    list already probes and caches it.
    """
    if not bot.is_active:
        return
    if await cached_bot_liveness(bot.id) is False:
        raise HTTPException(
            status_code=409,
            detail="Bot was deleted in BotFather; archived bots cannot be edited.",
        )


async def _drop_config_cache(*, token: str | None, token_hash: str | None) -> None:
    """Refresh a bot's config cache after a write, hash-keyed (S0.3).

    The raw token is the normal input (hashing happens at the cache boundary).
    A row whose plaintext has already been cleared still carries ``token_hash``,
    so invalidation never silently no-ops — that is the only case the
    hash-form entry point exists for.
    """
    if token is not None:
        await invalidate_bot_config(token)
    elif token_hash is not None:
        await invalidate_bot_config_hash(token_hash)
    else:  # pragma: no cover - neither a token nor a hash: nothing to drop
        logger.warning("Cannot invalidate config cache: no token or hash available")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/bots", response_model=list[BotSummary])
async def list_bots(auth: int = Depends(_require_auth)) -> list[BotSummary]:
    """All bots owned by the authenticated user (newest first).

    Active bots get a cached liveness probe: Telegram sends no "bot deleted"
    event, so a bot killed in BotFather would otherwise keep looking healthy.
    Probed bots that reject their token come back as ``state="archived"`` so
    the dashboard can move them out of the working list. Paused bots are not
    probed — they are already switched off.
    """
    async with session_scope() as session:
        result = await session.execute(
            select(BotModel)
            .join(BotModel.owner)
            .where(User.telegram_id == auth)
            .order_by(BotModel.created_at.desc())
        )
        bots = list(result.scalars())
        # Vault-first token accessor (S0.3): the plaintext column is the
        # transitional fallback, never read directly here.
        tokens = await resolve_bot_tokens(session, bots)
        probes: list[tuple[int, str]] = []
        for bot in bots:
            token = tokens.get(bot.id) if bot.is_active else None
            if token:
                probes.append((bot.id, token))
        alive = await probe_bots(probes)
        return [_summarize(bot, alive=alive.get(bot.id)) for bot in bots]


@router.get("/bots/{bot_id}", response_model=BotSummary)
async def get_bot(bot_id: int, auth: int = Depends(_require_auth)) -> BotSummary:
    """One owned bot (404 for bots that don't exist or aren't yours)."""
    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
        if bot is None:
            raise HTTPException(status_code=404, detail="Bot not found")
        token = await resolve_bot_token(session, bot) if bot.is_active else None
        alive = await bot_is_alive(bot.id, token) if token else None
        return _summarize(bot, alive=alive)


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
        await _reject_if_archived(bot)
        bot.config.flow = parsed.model_dump()
        bot.bot_type = parsed.bot_type
        # Vault-first (S0.3): the real token is what invalidates the hash-keyed
        # cache entry, so it is resolved before the session closes.
        token = await resolve_bot_token(session, bot)
        token_hash_value = bot.token_hash

    await _drop_config_cache(token=token, token_hash=token_hash_value)
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

        # Re-probing on toggle keeps the response consistent with the list
        # endpoint (a bot archived by Telegram stays archived when paused).
        token = await resolve_bot_token(session, bot)
        alive = await bot_is_alive(bot.id, token) if bot.is_active and token else None
        summary = _summarize(bot, alive=alive)
        token_hash_value = bot.token_hash  # captured before the session closes

    await _drop_config_cache(token=token, token_hash=token_hash_value)
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


@router.delete("/bots/{bot_id}", status_code=204)
async def delete_bot(bot_id: int, auth: int = Depends(_require_auth)) -> Response:
    """Permanently remove one of the owner's bots (archive cleanup).

    Telegram has no "bot deleted" event, so a bot killed in BotFather only
    shows up as ``state="archived"`` (see :mod:`tme.services.bot_health`);
    this is the explicit removal. The row, its config, its vaulted token and
    both cache entries go with it — vault first, then the row, then the caches.
    """
    async with session_scope() as session:
        bot = await _load_owned_bot(session, bot_id, auth)
        if bot is None:
            raise HTTPException(status_code=404, detail="Bot not found")
        token = await resolve_bot_token(session, bot)
        token_hash_value = bot.token_hash  # captured before the session closes
        await delete_bot_token(session, bot_id=bot_id)
        await session.delete(bot)

    await forget_bot_liveness(bot_id)
    await _drop_config_cache(token=token, token_hash=token_hash_value)
    logger.info("Deleted bot id=%s (owner tg=%s)", bot_id, auth)
    return Response(status_code=204)

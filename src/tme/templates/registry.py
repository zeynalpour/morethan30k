"""The template registry core — versioned, in-code seed data (Phase 2 S2.1).

A *template* is seed data for a :class:`~tme.schemas.bot_config.BotConfigUnion`
flow: a named, versioned snapshot (welcome copy, menu buttons, fallback, …)
held entirely in code under ``tme.templates``. Instantiating a template adds
**no handler, no ``BotType`` member and no per-template code** — a bot made
from a template is an ordinary ``bot_configs`` row (north-star: JSON in
Postgres → Redis cache → one shared engine).

This module is the registry core: the :class:`TemplateSpec` model, the
versioned :data:`REGISTRY` mapping and its lookup helpers. The shipped seed
entries live in :mod:`tme.templates.builtin`, which the package ``__init__``
imports — so any import of this module (the parent package always initializes
first) sees a populated registry. Nothing here touches Postgres or Redis: the
registry imports with zero infrastructure and is never cached.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

from tme.database.models import BotType
from tme.schemas.bot_config import BotConfigUnion

__all__ = [
    "PRESERVABLE_KEYS",
    "REGISTRY",
    "STAMP_KEY",
    "TemplateSpec",
    "clone_flow",
    "default_seed_for",
    "get_template",
    "list_templates",
    "register",
    "resolve_template",
    "set_default_seed",
    "stamp_of",
    "template_updates",
]


#: Flow rider key carrying a clone's provenance — ``{"id": ..., "version": ...}``.
#: S2.1 stamped seeds with it; S2.3 reads it to answer "which seed is this
#: bot from, and is the registry ahead of it?".
STAMP_KEY = "template"

#: Owner-controlled flow keys a re-clone may carry over from the OLD flow
#: onto the fresh seed (the Architect's ``preserve`` whitelist — the Phase 1
#: i18n layer is the owner's, never the template's). Never a generic
#: deep-merge: each listed key is copied verbatim, nothing else.
PRESERVABLE_KEYS = ("translations", "single_language")


def _stamp_of(flow: dict) -> dict | None:
    """The flow's provenance rider, or ``None`` for unstamped/legacy flows.

    Tolerant on purpose: the stamp is ``extra="allow"`` data, so a legacy
    row (pre-Phase-2), a scratch bot, or a hand-edited flow may carry
    nothing — or something malformed — where the stamp would be. A rider
    that is not a dict, or lacks a usable ``id``/``version``, reads as
    "no provenance" (the adoption path), never as an error.

    Returns a dict with EXACTLY the keys ``id`` and ``version`` (a
    well-formed stamp may carry extra metadata that callers must not
    serialize into comparisons or API responses).
    """
    stamp = flow.get(STAMP_KEY)
    if not isinstance(stamp, dict):
        return None
    template_id = stamp.get("id")
    version = stamp.get("version")
    if not isinstance(template_id, str) or not isinstance(version, int):
        return None
    return {"id": template_id, "version": version}


def stamp_of(flow: dict) -> dict | None:
    """Public read of a flow's provenance (see :func:`_stamp_of`)."""
    return _stamp_of(flow)


def resolve_template(flow: dict) -> TemplateSpec:
    """Resolve the template a flow's stamp records — the PINNED version.

    The stamp pins: it answers "which seed did this clone come from", so the
    pinned spec (not the latest) is returned. Callers wanting "is there
    something newer" use :func:`template_updates`; callers wanting "give me
    the current seed" use :func:`get_template` with no version.

    Raises:
        KeyError: The flow carries no usable stamp, or the stamp references
            an id/version no longer in the registry (deleted template, or a
            clone of a since-removed version).
    """
    stamp = _stamp_of(flow)
    if stamp is None:
        raise KeyError("flow carries no template provenance stamp")
    return get_template(stamp["id"], version=stamp["version"])


def template_updates(flow: dict) -> TemplateSpec | None:
    """The latest spec for the flow's template when the registry is AHEAD.

    Pure read (no Postgres, no Redis, no background job — "update available"
    is a dashboard badge, never a silent mutation). Returns ``None`` when the
    flow is unstamped (scratch/legacy — no update to offer), the template is
    unknown (deleted from the registry), or the clone already carries the
    latest version. A NEWER version is an explicit, owner-confirmed re-clone
    away; existing clones are never touched by a bump.
    """
    stamp = _stamp_of(flow)
    if stamp is None:
        return None
    try:
        latest = get_template(stamp["id"])
    except KeyError:
        return None  # template removed from the registry — nothing to offer
    return latest if latest.version > stamp["version"] else None


def clone_flow(spec: TemplateSpec, old_flow: dict, preserve: str | list[str] | None) -> dict:
    """Build the flow a re-clone persists: the fresh seed + carried keys.

    The base copy is replaced WHOLESALE by ``spec.seed`` (a naive merge over
    ``translations`` + ``menu_buttons`` would silently produce half-old
    half-new copy — worse than a clean reset). Then the whitelisted
    ``preserve`` keys are copied verbatim from the OLD flow onto the new one:
    ``translations`` (overlay semantics make owner translations safe over a
    changed base) and ``single_language`` (an owner decision a seed must
    never flip). The result carries the new stamp by construction — the
    seed itself is stamped at registry time.

    ``preserve=None`` defaults to the full whitelist (S2.3's owner workflow:
    the Phase 1 layer survives every reset unless the owner opts out).
    Unknown keys raise — the whitelist is closed, never a generic deep-merge.
    """
    if preserve is None:
        preserve = list(PRESERVABLE_KEYS)
    elif isinstance(preserve, str):
        preserve = [preserve]
    unknown = [key for key in preserve if key not in PRESERVABLE_KEYS]
    if unknown:
        raise ValueError(f"keys not preservable: {', '.join(sorted(unknown))}")

    flow = spec.seed.model_dump()
    for key in preserve:
        if key in old_flow:
            flow[key] = old_flow[key]
    return flow


class TemplateSpec(BaseModel):
    """One versioned template entry — a named ``BotConfig`` seed snapshot.

    Attributes:
        id: Slug identifying the template family (``^[a-z0-9_]{2,32}$`` —
            short enough to fit Telegram's 64-byte ``callback_data`` cap
            behind the S2.2 ``tmpl:`` prefix).
        version: Seed revision, bumped on any seed change. A clone is a
            *copy*, so bumping never mutates already-cloned bots; the latest
            version wins for new clones.
        bot_type: The existing PG enum member the seed's flow discriminator
            must match (never a new member — templates select, not extend).
        display_name: English base name (localized through ``MAIN_BOT_STRINGS``
            keys in S2.2, per the S1.3 controller-copy convention).
        description: English one-liner shown on the picker card.
        seed: The typed config the shared flow engine runs, validated through
            the same discriminated union the runtime uses — a template can
            never carry a config the engine would reject. Each shipped seed
            carries its own provenance stamp (the ``template`` flow rider; see
            :mod:`tme.templates.builtin`).
    """

    id: str = Field(
        ...,
        pattern=r"^[a-z0-9_]{2,32}$",
        description="Template slug.",
    )
    version: int = Field(..., ge=1, description="Seed revision; bumped on any seed change.")
    bot_type: BotType = Field(..., description="The bot type this template seeds.")
    display_name: str = Field(..., min_length=1, max_length=64, description="Picker-card title.")
    description: str = Field(..., min_length=1, max_length=280, description="Picker-card blurb.")
    seed: BotConfigUnion = Field(..., description="The seeded config flow.")

    @model_validator(mode="after")
    def _seed_matches_declared_type(self) -> Self:
        """The flow discriminator must agree with the spec's ``bot_type``.

        ``PATCH /api/bots/{id}/config`` enforces the same invariant at write
        time; the registry must not violate it at the source.
        """
        if self.seed.bot_type != self.bot_type:
            raise ValueError(
                f"template {self.id!r} v{self.version}: seed bot_type "
                f"{self.seed.bot_type!r} does not match declared {self.bot_type!r}"
            )
        return self


#: The registry proper: template id → specs ordered by ascending ``version``.
#: Populated at import time by :mod:`tme.templates.builtin`, in registration
#: (picker-card) order. Pure in-memory data — never cached, never persisted.
REGISTRY: dict[str, list[TemplateSpec]] = {}

#: Per-type default seeds — what a bot of each type gets when created
#: **without** picking a template (S2.2's "start from scratch" card). Pinned
#: at import by :func:`set_default_seed` so it can never drift from
#: ``_default_config_for`` between releases; S2.3 collapses the two sources.
_DEFAULT_SEED_FOR_TYPE: dict[BotType, BotConfigUnion] = {}


def register(spec: TemplateSpec) -> None:
    """Add a spec to :data:`REGISTRY`, keeping per-id versions ascending.

    Adding a template = calling this with a new spec — zero engine, router,
    or enum changes (S2.1 acceptance criterion).

    Raises:
        ValueError: The ``(id, version)`` pair is already registered.
    """
    versions = REGISTRY.setdefault(spec.id, [])
    if any(existing.version == spec.version for existing in versions):
        raise ValueError(f"template {spec.id!r} version {spec.version} is already registered")
    versions.append(spec)
    versions.sort(key=lambda existing: existing.version)


def set_default_seed(bot_type: BotType, seed: BotConfigUnion) -> None:
    """Record the default seed for ``bot_type`` (called once per type by ``builtin``).

    Raises:
        ValueError: ``seed`` belongs to a different bot type.
    """
    if seed.bot_type != bot_type:
        raise ValueError(f"default seed for {bot_type!r} carries bot_type {seed.bot_type!r}")
    _DEFAULT_SEED_FOR_TYPE[bot_type] = seed


def get_template(template_id: str, version: int | None = None) -> TemplateSpec:
    """Look up a spec by id — a pinned ``version`` or the latest.

    Args:
        template_id: Template slug.
        version: Exact version to pin; ``None`` resolves the latest.

    Returns:
        The matching :class:`TemplateSpec` — the registered instance; treat
        it as read-only (deep-copy the ``seed`` before mutating it).

    Raises:
        KeyError: Unknown ``template_id``, or no such ``version`` for it.
    """
    versions = REGISTRY.get(template_id)
    if not versions:
        raise KeyError(f"unknown template id {template_id!r}")
    if version is None:
        return versions[-1]
    for spec in versions:
        if spec.version == version:
            return spec
    raise KeyError(f"template {template_id!r} has no version {version}")


def list_templates() -> list[TemplateSpec]:
    """The latest spec of every id, in registration order (picker source)."""
    return [versions[-1] for versions in REGISTRY.values()]


def default_seed_for(bot_type: BotType) -> BotConfigUnion:
    """The seed a bot of ``bot_type`` gets when created without a template pick.

    Returns a **fresh deep copy** per call (the caller may adjust it before
    persisting — S2.2/S2.3 — without poisoning the in-code registry), unlike
    :func:`_default_config_for`, whose output the latest seed is pinned to
    equal-or-superset (see ``tests/test_templates.py``).

    Raises:
        KeyError: The type has no default seed (``bridge`` / ``ai_gateway``
            today — no shipped template and no bare starter).
    """
    try:
        seed = _DEFAULT_SEED_FOR_TYPE[bot_type]
    except KeyError:
        raise KeyError(f"no default seed for bot type {bot_type!r}") from None
    return seed.model_copy(deep=True)

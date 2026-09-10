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
    "REGISTRY",
    "TemplateSpec",
    "default_seed_for",
    "get_template",
    "list_templates",
    "register",
    "set_default_seed",
]


class TemplateSpec(BaseModel):
    """One versioned template entry — a named ``BotConfig`` seed snapshot.

    Attributes:
        id: Slug identifying the template family (``^[a-z0-9_]{2,32}$`` —
            short enough to fit Telegram's 64-byte ``callback_data`` cap
            behind the S2.2 ``pick:`` prefix).
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

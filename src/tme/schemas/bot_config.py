"""Pydantic models describing a tenant bot's *configuration-driven* behaviour.

This is the contract for the JSON that lives in ``bot_configs.flow`` (Postgres),
is cached in Redis, and is read by the dynamic tenant router at runtime. Keeping
it as a validated schema means a malformed config fails fast at write time
instead of crashing a handler for a live user.

Each :class:`BotType` maps to a dedicated config variant. The generic schema
(the historical ``BotConfigSchema``) is the most feature-rich and forward
compatible; Hello/Echo are thin, purpose-built variants plugged into the
:class:`BotConfigUnion` discriminated union keyed on ``bot_type``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from tme.database.models import BotType


class MenuButton(BaseModel):
    """A single inline-keyboard button in a tenant's menu."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=64, description="Button caption.")
    # Exactly one of the following should be set. `callback` drives internal
    # flow navigation; `url` opens a link. Validation is intentionally lenient
    # for the MVP — the flow engine treats a missing target as a no-op.
    callback: str | None = Field(
        default=None,
        max_length=64,
        description="callback_data routed back into the flow engine.",
    )
    url: str | None = Field(
        default=None, description="Opens an external URL instead of a callback."
    )


class BotConfigBase(BaseModel):
    """Shared fields for every bot-config variant.

    The MVP flow engine only consumes ``welcome_message`` and ``menu_buttons``,
    but the surrounding fields (versioning, active modules, arbitrary extras)
    let the schema grow without a migration. Each variant declares its own
    ``bot_type`` discriminator (used by :class:`BotConfigUnion`); the variants
    are **siblings**, not subclasses of the generic config, so a Hello config
    can never be typed as (or mistaken for) a generic one.
    """

    model_config = ConfigDict(extra="allow")  # forward-compat: keep unknown keys.

    version: int = Field(default=1, ge=1, description="Config schema version.")
    welcome_message: str = Field(
        default="👋 Welcome!",
        description="Text sent in response to /start (and as the flow root).",
    )
    menu_buttons: list[MenuButton] = Field(
        default_factory=list,
        description="Inline buttons rendered under the welcome message.",
    )
    active_modules: list[str] = Field(
        default_factory=list,
        description="Feature flags / module names enabled for this bot.",
    )
    fallback_message: str = Field(
        default="🤖 Sorry, I didn't understand that.",
        description="Reply used when no rule matches the incoming update.",
    )


class BotConfigSchema(BotConfigBase):
    """The **generic** config — the most feature-rich, forward-compatible variant.

    It is the historical ``BotConfigSchema`` and the fallback for legacy rows.
    ``bot_type`` is the discriminator used by :class:`BotConfigUnion`.
    """

    bot_type: Literal[BotType.GENERIC] = BotType.GENERIC

    @classmethod
    def default(cls) -> BotConfigSchema:
        """Return a sensible starter config for a freshly-provisioned bot."""
        return cls(
            welcome_message="👋 Welcome! This bot was created on the TME platform.",
            menu_buttons=[
                MenuButton(text="About", callback="about"),
                MenuButton(text="Contact", callback="contact"),
            ],
        )


class HelloBotConfig(BotConfigBase):
    """A conversational greeting bot — nice extras on top of the common base."""

    bot_type: Literal[BotType.HELLO] = BotType.HELLO
    greeting: str = Field(
        default="Hello there!",
        description="Greeting sent whenever /start is pressed (generic welcome is ignored).",
    )


class EchoBotConfig(BotConfigBase):
    """Echoes every non-command message back to the sender."""

    bot_type: Literal[BotType.ECHO] = BotType.ECHO
    echo_prefix: str = Field(
        default="",
        description="Optional prefix rendered before the echoed text.",
    )


#: Discriminated union covering every provisionable bot type. At JSON parse
#: time Pydantic picks the right variant from the ``bot_type`` literal, so a
#: stray ``bot_type`` (or a missing one) fails validation instead of being
#: silently treated as the wrong bot kind.
BotConfigUnion = Annotated[
    BotConfigSchema | HelloBotConfig | EchoBotConfig,
    Field(discriminator="bot_type"),
]

# BRIDGE and AI_GATEWAY are declared on the enum but have no dedicated schema
# yet — an explicit ``bot_type`` for them fails the strict union and falls back
# to the generic variant via :func:`parse_bot_config` (which also resolves a
# missing ``bot_type``). Add dedicated classes here (plus the matching enum
# member in :mod:`tme.database.models`) as those phases land.


def parse_bot_config(data: dict) -> BotConfigUnion:
    """Parse a stored flow dict into the right typed variant.

    ``data`` is a raw dictionary as persisted in ``bot_configs.flow`` / Redis.
    If it carries a ``bot_type`` discriminator it is parsed via the discriminated
    union, otherwise (legacy rows that predate S0.2) it is treated as a generic
    config. An explicit-but-unknown ``bot_type`` is treated as generic too, so a
    config never becomes unroutable at read time.
    """
    try:
        return TypeAdapter(BotConfigUnion).validate_python(data)
    except ValidationError:
        # Missing/invalid discriminator → legacy row (or an as-yet-unmodelled
        # type); fall back to the generic base. Drop any malformed bot_type so
        # the generic variant's literal default (generic) applies. A wholly
        # non-dict flow (corrupt JSONB) degrades to the generic default too —
        # a config must never become unroutable at read time.
        if not isinstance(data, dict):
            data = {}
        safe = {k: v for k, v in data.items() if k != "bot_type"}
        return BotConfigSchema.model_validate(safe)


def dump_bot_config(config: BotConfigUnion) -> bytes:
    """Serialise a config for the Redis cache.

    Every parsed config already carries its ``bot_type`` discriminator (the
    generic default fills it for legacy rows), so a plain JSON dump round-trips.
    """
    return config.model_dump_json().encode()

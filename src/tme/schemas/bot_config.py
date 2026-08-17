"""Pydantic models describing a tenant bot's *configuration-driven* behaviour.

This is the contract for the JSON that lives in ``bot_configs.flow`` (Postgres),
is cached in Redis, and is read by the dynamic tenant router at runtime. Keeping
it as a validated schema means a malformed config fails fast at write time
instead of crashing a handler for a live user.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class BotConfigSchema(BaseModel):
    """The full, validated runtime configuration for one tenant bot.

    The MVP flow engine only consumes ``welcome_message`` and ``menu_buttons``,
    but the surrounding fields (versioning, active modules, arbitrary extras)
    are here so the schema can grow without a migration.
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

    @classmethod
    def default(cls) -> BotConfigSchema:
        """Return a sensible starter config for a freshly-provisioned bot."""
        return cls(
            welcome_message="👋 Welcome! This bot was created on the TME platform.",
            menu_buttons=[
                MenuButton(text="ℹ️ About", callback="about"),
                MenuButton(text="📞 Contact", callback="contact"),
            ],
        )

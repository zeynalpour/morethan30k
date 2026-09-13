"""Per-bot copy localization — Phase 1 multilanguage platform.

A tenant bot's base flow is written in ONE language (its default). The owner
may add per-language overrides under ``config.translations``; this module
resolves the *effective copy* for a single user with the fallback chain
documented in ROADMAP Phase 1: **user language → English → bot default**,
applied per field (a translation that only overrides the welcome message keeps
the base menu and fallback text).
"""

from __future__ import annotations

from dataclasses import dataclass

from tme.schemas.bot_config import (
    BotConfigBase,
    BotConfigUnion,
    EchoBotConfig,
    HelloBotConfig,
    MenuButton,
    Translation,
)

#: Fallbacks for blank copy — read from the models so the default exists in
#: exactly one place (a flow that stored "" would otherwise ship empty text).
_DEFAULT_WELCOME: str = BotConfigBase.model_fields["welcome_message"].default
_DEFAULT_FALLBACK: str = BotConfigBase.model_fields["fallback_message"].default
_DEFAULT_GREETING: str = HelloBotConfig.model_fields["greeting"].default


@dataclass
class LocalizedCopy:
    """The effective user-facing copy for one user, resolved per field."""

    welcome_message: str
    fallback_message: str
    greeting: str
    echo_prefix: str
    menu_buttons: list[MenuButton]


def normalize_language(language_code: str | None) -> str:
    """Collapse a Telegram ``language_code`` to ISO-639-1.

    ``"en-US"`` / ``"EN"`` → ``"en"``; missing/garbage → ``""`` (which never
    matches a translation key, so the caller falls through the chain).
    """
    if not language_code:
        return ""
    return language_code.replace("_", "-").split("-", 1)[0].strip().lower()


def effective_language(stored: str | None, telegram: str | None) -> str | None:
    """The language that applies to a user: explicit choice wins.

    ``stored`` is the user's ``/language`` preference (already normalized),
    ``telegram`` their Telegram UI ``language_code``. Returns ``None`` when
    neither yields anything (→ full fallback chain in :func:`localize`).
    """
    if stored:
        return stored
    normalized = normalize_language(telegram)
    return normalized or None


def _non_empty(value: str | None) -> str | None:
    """Treat blank/whitespace copy as "unset" so the chain falls through.

    An empty string is never valid user-facing copy — sending it makes
    Telegram reject the message ("message text is empty") and the bot looks
    dead. Owners clear a field in the dashboard, so blanks must behave like
    "not provided", not like "override with nothing".
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def localize(config: BotConfigUnion, language_code: str | None) -> LocalizedCopy:
    """Resolve the copy for a user's language (base → English → user wins).

    Fallback chain, per field: the user's language translation if present,
    else the ``en`` translation if present, else the base flow value. A
    translation may override any subset of fields.

    Blank values (base or translation) are skipped, and a field that ends up
    blank falls back to the model default — no handler ever sends empty text.
    """
    copy = LocalizedCopy(
        welcome_message=config.welcome_message,
        fallback_message=config.fallback_message,
        greeting=config.greeting if isinstance(config, HelloBotConfig) else "",
        echo_prefix=config.echo_prefix if isinstance(config, EchoBotConfig) else "",
        menu_buttons=list(config.menu_buttons),
    )

    # Single-language bots speak ONLY their base copy — no translation layers.
    if not config.single_language:
        # English first, then the user's own language — later layers win.
        layers = ["en"]
        user_lang = normalize_language(language_code)
        if user_lang and user_lang != "en":
            layers.append(user_lang)

        for code in layers:
            translation = config.translations.get(code)
            if translation is None:
                continue
            _overlay(copy, translation)

    # Final guard: blanks never reach the wire.
    copy.welcome_message = _non_empty(copy.welcome_message) or _DEFAULT_WELCOME
    copy.fallback_message = _non_empty(copy.fallback_message) or _DEFAULT_FALLBACK
    if isinstance(config, HelloBotConfig):
        copy.greeting = _non_empty(copy.greeting) or _DEFAULT_GREETING
    copy.echo_prefix = copy.echo_prefix or ""
    return copy


def _overlay(copy: LocalizedCopy, translation: Translation) -> None:
    """Apply a translation's provided (non-blank) fields over the running copy."""
    if _non_empty(translation.welcome_message) is not None:
        copy.welcome_message = translation.welcome_message  # type: ignore[assignment]
    if _non_empty(translation.fallback_message) is not None:
        copy.fallback_message = translation.fallback_message  # type: ignore[assignment]
    if translation.greeting is not None and _non_empty(translation.greeting) is not None:
        copy.greeting = translation.greeting
    if translation.echo_prefix is not None:
        copy.echo_prefix = translation.echo_prefix
    if translation.menu_buttons is not None:
        copy.menu_buttons = list(translation.menu_buttons)

"""Tests for Phase 1 S1.1 — per-bot translations + localized copy resolution."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
import pytest

from tme.core.i18n import localize, normalize_language
from tme.schemas.bot_config import BotConfigUnion


def _config(**overrides) -> BotConfigUnion:
    base = {
        "bot_type": "generic",
        "welcome_message": "Welcome (base)",
        "fallback_message": "Fallback (base)",
        "menu_buttons": [{"text": "Base Button", "callback": "base"}],
        "translations": {
            "fa": {
                "welcome_message": "خوش آمدید (fa)",
                "fallback_message": "پاسخ پیش‌فرض (fa)",
            },
            "en": {
                "welcome_message": "Welcome (en)",
                "menu_buttons": [{"text": "English", "callback": "base"}],
            },
        },
        **overrides,
    }
    return TypeAdapter(BotConfigUnion).validate_python(base)


# ------------------------------------------------------------------ normalize
def test_normalize_language_collapses_to_iso639_1() -> None:
    assert normalize_language("en-US") == "en"
    assert normalize_language("EN") == "en"
    assert normalize_language("pt_br") == "pt"
    assert normalize_language(None) == ""
    assert normalize_language("") == ""


# ---------------------------------------------------------------- localize
def test_user_language_wins_over_english_and_base() -> None:
    copy = localize(_config(), "fa")
    assert copy.welcome_message == "خوش آمدید (fa)"
    assert copy.fallback_message == "پاسخ پیش‌فرض (fa)"


def test_partial_translation_falls_back_per_field() -> None:
    # fa overrides welcome + fallback but no menu → English menu is the
    # fallback (en layer), then the base flow.
    copy = localize(_config(), "fa")
    assert copy.welcome_message == "خوش آمدید (fa)"
    assert copy.fallback_message == "پاسخ پیش‌فرض (fa)"
    assert [b.text for b in copy.menu_buttons] == ["English"]


def test_english_translation_used_when_user_language_missing() -> None:
    copy = localize(_config(), "de")
    assert copy.welcome_message == "Welcome (en)"
    assert copy.menu_buttons[0].text == "English"
    # en has no fallback → base fallback.
    assert copy.fallback_message == "Fallback (base)"


def test_unknown_language_gets_english_copy() -> None:
    copy = localize(_config(), None)
    assert copy.welcome_message == "Welcome (en)"
    assert copy.menu_buttons[0].text == "English"


def test_region_specific_language_code_matches_base_language() -> None:
    copy = localize(_config(), "en-US")
    assert copy.welcome_message == "Welcome (en)"


def test_user_language_overrides_english_when_both_exist() -> None:
    cfg = _config(
        translations={
            "en": {"welcome_message": "Hello (en)"},
            "fa": {"welcome_message": "سلام (fa)"},
        }
    )
    assert localize(cfg, "fa").welcome_message == "سلام (fa)"
    assert localize(cfg, "en").welcome_message == "Hello (en)"


# ------------------------------------------------------------ type-specific
def test_hello_greeting_localizes() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "hello",
            "greeting": "Hello (base)",
            "translations": {"fa": {"greeting": "سلام (fa)"}},
        }
    )
    assert localize(cfg, "fa").greeting == "سلام (fa)"
    assert localize(cfg, "de").greeting == "Hello (base)"


def test_echo_prefix_localizes() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "echo",
            "echo_prefix": ">> ",
            "translations": {"fa": {"echo_prefix": "« "}},
        }
    )
    assert localize(cfg, "fa").echo_prefix == "« "
    assert localize(cfg, "de").echo_prefix == ">> "


# ------------------------------------------------------------------ schema
def test_translations_round_trip_through_parse() -> None:
    flow = {
        "bot_type": "generic",
        "welcome_message": "w",
        "translations": {"fa": {"welcome_message": "f"}},
    }
    cfg = TypeAdapter(BotConfigUnion).validate_python(flow)
    dumped = cfg.model_dump()
    assert dumped["translations"]["fa"]["welcome_message"] == "f"
    reparsed = TypeAdapter(BotConfigUnion).validate_python(dumped)
    assert reparsed.translations["fa"].welcome_message == "f"


def test_translation_buttons_validate_like_base_buttons() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BotConfigUnion).validate_python(
            {"bot_type": "generic", "translations": {"fa": {"menu_buttons": [{"callback": "x"}]}}}
        )

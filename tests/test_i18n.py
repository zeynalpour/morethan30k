"""Tests for Phase 1 S1.1 — per-bot translations + localized copy resolution."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
import pytest

from tme.core.i18n import effective_language, localize, normalize_language
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


# ---------------------------------------------------------- effective language
def test_effective_language_preference_wins_over_telegram() -> None:
    assert effective_language("fa", "en-US") == "fa"
    assert effective_language(None, "en-US") == "en"
    assert effective_language("", "de") == "de"
    assert effective_language(None, None) is None
    assert effective_language("", None) is None


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


# ------------------------------------------------------------ single-language
def test_single_language_bot_ignores_translations_and_user_language() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "generic",
            "single_language": True,
            "welcome_message": "Base only",
            "translations": {
                "en": {"welcome_message": "English"},
                "fa": {"welcome_message": "فارسی"},
            },
        }
    )
    assert localize(cfg, "fa").welcome_message == "Base only"
    assert localize(cfg, "en").welcome_message == "Base only"
    assert localize(cfg, None).welcome_message == "Base only"


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


# --------------------------------------------------------------------------- #
# Blank copy never reaches the wire (owner-reported: empty text → bot "dead")
# --------------------------------------------------------------------------- #
def test_blank_translation_does_not_blank_the_base() -> None:
    """An empty override means "unset", not "say nothing"."""
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "generic",
            "welcome_message": "base hello",
            "translations": {"en": {"welcome_message": ""}},
        }
    )
    assert localize(cfg, "en").welcome_message == "base hello"


def test_blank_base_copy_falls_back_to_the_model_default() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {"bot_type": "generic", "welcome_message": "", "fallback_message": "   "}
    )
    copy = localize(cfg, "en")
    assert copy.welcome_message.strip()
    assert copy.fallback_message.strip()


def test_blank_hello_greeting_falls_back_to_the_default() -> None:
    """The exact bug: a hello bot whose greeting was cleared sent empty text
    and Telegram rejected every /start with "message text is empty"."""
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {"bot_type": "hello", "greeting": "", "welcome_message": ""}
    )
    copy = localize(cfg, "en")
    assert copy.greeting.strip()
    assert copy.welcome_message.strip()


# --------------------------------------------------------------------------- #
# Issue #23 — main_language drives the MIDDLE layer, per-step copy rides the chain
# --------------------------------------------------------------------------- #
def _chain_config(**overrides) -> BotConfigUnion:
    """A Persian-base bot with en / fa / de translations (issue #23)."""
    base = {
        "bot_type": "generic",
        "welcome_message": "خوش آمدید (base)",
        "fallback_message": "پاسخ پیش‌فرض (base)",
        "active_modules": ["steps"],
        "steps": [
            {
                "id": "rating",
                "prompt": "قیمت بده (base)",
                "options": [
                    {"label": "خوب (base)", "value": "good"},
                    {"label": "بد (base)", "value": "bad"},
                ],
            }
        ],
        "translations": {
            "en": {
                "welcome_message": "Welcome (en)",
                "steps": {"rating": {"prompt": "Rate us (en)", "options": ["Good (en)"]}},
            },
            "fa": {
                "welcome_message": "خوش آمدید (fa)",
                # Blank label index 0 = "unset" → that option falls through.
                "steps": {"rating": {"prompt": "امتیاز بده (fa)", "options": ["", "بد (fa)"]}},
            },
            "de": {
                "welcome_message": "Willkommen (de)",
                "steps": {"rating": {"prompt": "Bewerten (de)"}},
            },
        },
        **overrides,
    }
    return TypeAdapter(BotConfigUnion).validate_python(base)


def test_without_main_language_english_stays_the_middle_layer() -> None:
    """Unset main_language = today's behaviour, byte for byte."""
    cfg = _chain_config()
    assert cfg.main_language is None
    # A language the bot does not translate falls to the English layer.
    assert localize(cfg, "tr").welcome_message == "Welcome (en)"
    assert localize(cfg, None).welcome_message == "Welcome (en)"
    # The fa translation is NOT a fallback for other languages.
    assert localize(cfg, "tr").fallback_message == "پاسخ پیش‌فرض (base)"


def test_main_language_replaces_the_middle_layer() -> None:
    cfg = _chain_config(main_language="fa")
    # No translation for the user's language → the MAIN language applies.
    assert localize(cfg, "tr").welcome_message == "خوش آمدید (fa)"
    # No language at all → the middle layer, then the base copy.
    assert localize(cfg, None).welcome_message == "خوش آمدید (fa)"
    # The middle layer is not English any more.
    assert "Welcome (en)" not in localize(cfg, "tr").welcome_message


def test_user_language_still_wins_over_the_main_language() -> None:
    """Only the MIDDLE layer moves — the order of the chain is unchanged."""
    cfg = _chain_config(main_language="fa")
    assert localize(cfg, "de").welcome_message == "Willkommen (de)"
    assert localize(cfg, "en").welcome_message == "Welcome (en)"
    # A user whose language IS the main language resolves to it directly.
    assert localize(cfg, "fa").welcome_message == "خوش آمدید (fa)"
    # Region-tagged user codes collapse first (unchanged behaviour).
    assert localize(cfg, "fa-IR").welcome_message == "خوش آمدید (fa)"


def test_blank_main_language_is_unset_and_keeps_english_middle_layer() -> None:
    cfg = _chain_config(main_language="   ")
    assert cfg.main_language is None
    assert localize(cfg, "tr").welcome_message == "Welcome (en)"


def test_single_language_still_bypasses_main_language_and_step_copy() -> None:
    """single_language behaviour is untouched: base copy only, every layer.

    The copy carries NO step overrides, so the engine (which supplies the
    flow's own prompt/label as the fallback) renders the base step copy.
    """
    cfg = _chain_config(main_language="fa", single_language=True)
    copy = localize(cfg, "fa")
    assert copy.welcome_message == "خوش آمدید (base)"
    assert copy.steps == {}
    assert copy.step_prompt("rating", "قیمت بده (base)") == "قیمت بده (base)"
    assert copy.step_options("rating", ["L1", "L2"]) == ["L1", "L2"]


def test_step_copy_follows_the_same_fallback_chain() -> None:
    # de supplies a prompt but no option labels → the en middle layer's label
    # applies to index 0 and the base label to the index en does not cover.
    copy = localize(_chain_config(), "de")
    assert copy.step_prompt("rating", "base prompt") == "Bewerten (de)"
    assert copy.step_options("rating", ["Good (base)", "Bad (base)"]) == [
        "Good (en)",
        "Bad (base)",
    ]

    # main_language="fa" → a tr user gets the fa prompt, and the blank fa
    # label falls through while the provided one wins.
    copy = localize(_chain_config(main_language="fa"), "tr")
    assert copy.step_prompt("rating", "قیمت بده (base)") == "امتیاز بده (fa)"
    assert copy.step_options("rating", ["خوب (base)", "بد (base)"]) == [
        "خوب (base)",
        "بد (fa)",
    ]


def test_step_copy_unknown_step_id_and_missing_entry_fall_through() -> None:
    for language in ("fa", "tr", None):
        copy = localize(_chain_config(main_language="fa"), language)
        assert copy.step_prompt("ghost", "base prompt") == "base prompt"
        assert copy.step_options("ghost", ["A", "B"]) == ["A", "B"]


def test_blank_step_overrides_are_unset() -> None:
    """A cleared step field (or a blank option label) never blanks a prompt."""
    cfg = TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "generic",
            "active_modules": ["steps"],
            "steps": [{"id": "a", "prompt": "base prompt"}],
            "translations": {"fa": {"steps": {"a": {"prompt": "   ", "options": ["", "عالی"]}}}},
        }
    )
    copy = localize(cfg, "fa")
    assert copy.step_prompt("a", "base prompt") == "base prompt"
    assert copy.step_options("a", ["L1", "L2"]) == ["L1", "عالی"]


def test_step_copy_is_absent_when_nothing_is_translated() -> None:
    """No per-step overrides → an empty map, so renderers can skip the work."""
    copy = localize(_config(), "fa")
    assert copy.steps == {}
    assert copy.step_prompt("anything", "base") == "base"

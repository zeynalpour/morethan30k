"""Tests for the tenant config schema (the JSON flow contract)."""

from __future__ import annotations

import json

from pydantic import ValidationError
import pytest

from tme.schemas.bot_config import (
    BotConfigSchema,
    MenuButton,
    dump_bot_config,
    parse_bot_config,
)


def test_default_config_has_welcome_and_buttons() -> None:
    cfg = BotConfigSchema.default()
    assert cfg.welcome_message
    assert len(cfg.menu_buttons) >= 1
    assert all(isinstance(b, MenuButton) for b in cfg.menu_buttons)


def test_json_round_trip_is_lossless() -> None:
    """The Redis cache stores configs as JSON — round-tripping must be stable."""
    cfg = BotConfigSchema.default()
    restored = BotConfigSchema.model_validate_json(cfg.model_dump_json())
    assert restored == cfg


def test_unknown_keys_are_preserved_for_forward_compat() -> None:
    """extra='allow' keeps future flow fields we don't model yet."""
    cfg = BotConfigSchema.model_validate(
        {"welcome_message": "hi", "some_future_field": {"nested": 1}}
    )
    dumped = cfg.model_dump()
    assert dumped["some_future_field"] == {"nested": 1}


def test_defaults_apply_to_sparse_config() -> None:
    cfg = BotConfigSchema.model_validate({})
    assert cfg.version == 1
    assert cfg.fallback_message  # non-empty default
    assert cfg.menu_buttons == []


# ---- S2.1: template provenance stamp inside the flow ----
def test_template_stamp_parses_and_round_trips() -> None:
    """A flow carrying {"template": {...}} parses via parse_bot_config.

    The stamp is an extra='allow' rider (S2.1 decision): it must survive the
    full Redis-cache round trip (dump_bot_config → re-parse) so S2.3 can
    tell which seed a bot came from, exactly as the write path preserves it
    via the dashboard's ...flow spread.
    """
    stamped = {
        "bot_type": "generic",
        "welcome_message": "hi",
        "template": {"id": "hello_world", "version": 1},
    }
    parsed = parse_bot_config(stamped)
    assert isinstance(parsed, BotConfigSchema)
    assert parsed.model_dump()["template"] == {"id": "hello_world", "version": 1}

    # Redis-cache round trip (dump_bot_config → parse_bot_config).
    restored = parse_bot_config(json.loads(dump_bot_config(parsed)))
    assert restored.model_dump()["template"] == {"id": "hello_world", "version": 1}
    assert restored == parsed


def test_legacy_row_without_stamp_still_parses() -> None:
    """Pre-Phase-2 rows have no rider; parse must stay lossless for them."""
    legacy = {"bot_type": "generic", "welcome_message": "legacy"}
    parsed = parse_bot_config(legacy)
    assert isinstance(parsed, BotConfigSchema)
    assert "template" not in parsed.model_dump()
    restored = parse_bot_config(json.loads(dump_bot_config(parsed)))
    assert restored == parsed


def test_stamp_survives_dashboard_style_spread_edit() -> None:
    """The dashboard saves with `...flow` spread — owner edits must keep the stamp."""
    stamped = {
        "bot_type": "generic",
        "welcome_message": "hi",
        "template": {"id": "quiz", "version": 1},
    }
    parsed = parse_bot_config(stamped)
    # Simulated owner edit: spread the existing flow, override one field.
    edited = {**parsed.model_dump(), "welcome_message": "edited"}
    re_parsed = parse_bot_config(edited)
    assert re_parsed.model_dump()["template"] == {"id": "quiz", "version": 1}


# ---- Issue #23: per-step translations + the bot's main language ----
def test_main_language_defaults_to_none() -> None:
    """Unset = today's behaviour (English middle layer) — never a stored ""."""
    assert BotConfigSchema.model_validate({}).main_language is None


def test_main_language_normalizes_region_and_case() -> None:
    """ISO-639-1, collapsed the same way translation keys are."""
    assert BotConfigSchema.model_validate({"main_language": "fa"}).main_language == "fa"
    assert BotConfigSchema.model_validate({"main_language": "FA-IR"}).main_language == "fa"
    assert BotConfigSchema.model_validate({"main_language": "fa_IR"}).main_language == "fa"


def test_blank_main_language_is_unset_not_empty_string() -> None:
    """A cleared selector must never persist "" — blank means "no main language".

    The dashboard omits a cleared field; this pins the backend's own guard so
    a stray "" from any client can't become a translation-chain key.
    """
    for blank in ("", "   ", "\n", "\t "):
        assert BotConfigSchema.model_validate({"main_language": blank}).main_language is None


def test_main_language_rejects_non_iso639_1() -> None:
    for bad in ("persian", "e", "english", "123", "فا"):
        with pytest.raises(ValidationError):
            BotConfigSchema.model_validate({"main_language": bad})


def test_step_translations_parse_and_round_trip() -> None:
    """Per-step copy rides the translations map (issue #23) and the Redis cache."""
    flow = {
        "bot_type": "generic",
        "active_modules": ["steps"],
        "main_language": "fa",
        "steps": [
            {
                "id": "rating",
                "prompt": "How would you rate us? ⭐",
                "options": [{"label": "😍 Excellent", "value": "excellent"}],
            }
        ],
        "translations": {
            "fa": {
                "steps": {
                    "rating": {
                        "prompt": "به ما چه امتیازی می‌دهید؟ ⭐",
                        "options": ["😍 عالی"],
                    }
                }
            }
        },
    }
    parsed = parse_bot_config(flow)
    assert parsed.main_language == "fa"

    steps = parsed.translations["fa"].steps
    assert steps is not None
    assert steps["rating"].prompt == "به ما چه امتیازی می‌دهید؟ ⭐"
    assert steps["rating"].options == ["😍 عالی"]

    restored = parse_bot_config(json.loads(dump_bot_config(parsed)))
    assert restored == parsed
    restored_steps = restored.translations["fa"].steps
    assert restored_steps is not None
    assert restored_steps["rating"].options == ["😍 عالی"]


def test_unknown_step_ids_are_stored_never_invented() -> None:
    """The schema keeps an id the owner typed and invents none of its own.

    It cannot know the flow's steps, so an override for a step that no longer
    exists is stored as given (and simply never read at render time) — a
    silent drop would lose the owner's copy without telling them.
    """
    flow = {
        "bot_type": "generic",
        "translations": {"fa": {"steps": {"ghost": {"prompt": "p"}}}},
    }
    steps = parse_bot_config(flow).translations["fa"].steps
    assert steps is not None
    assert list(steps) == ["ghost"]

    # An unset steps map stays None rather than becoming {}.
    assert (
        BotConfigSchema.model_validate({"translations": {"fa": {}}}).translations["fa"].steps
        is None
    )


def test_step_translation_keeps_unknown_keys_and_blank_values_verbatim() -> None:
    """``extra=allow`` forward-compat + "blank is unset" is a RESOLUTION rule.

    The schema stores what the owner typed (including blanks); the fallback
    chain is what treats a blank as unset, so a flow never silently loses an
    edit the owner made.
    """
    flow = {
        "bot_type": "generic",
        "translations": {
            "fa": {
                "steps": {"rating": {"prompt": "  ", "options": ["", "عالی"], "future_field": 1}}
            }
        },
    }
    entry = parse_bot_config(flow).translations["fa"].steps
    assert entry is not None
    assert entry["rating"].prompt == "  "
    assert entry["rating"].options == ["", "عالی"]
    assert entry["rating"].model_dump()["future_field"] == 1

"""Tests for the tenant config schema (the JSON flow contract)."""

from __future__ import annotations

import json

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

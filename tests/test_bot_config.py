"""Tests for the tenant config schema (the JSON flow contract)."""

from __future__ import annotations

from tme.schemas.bot_config import BotConfigSchema, MenuButton


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

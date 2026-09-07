"""Tests for S0.2 — BotType enum + per-type config union.

Covers the discriminated union parsing, the tolerant legacy-dict loader, and
that provisioning/typed defaults pick the right config variant.
"""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
import pytest

from tme.database.models import BotType
from tme.schemas.bot_config import (
    BotConfigSchema,
    BotConfigUnion,
    EchoBotConfig,
    HelloBotConfig,
    parse_bot_config,
)
from tme.services.managed_bots import _default_config_for


# --------------------------------------------------------------------- enum
def test_bot_type_members() -> None:
    assert {b.value for b in BotType} == {"generic", "hello", "echo", "bridge", "ai_gateway"}
    assert BotType.GENERIC == "generic"


# ---- discriminated union ----
def test_union_resolves_hello() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python({"bot_type": "hello", "greeting": "Hi"})
    assert isinstance(cfg, HelloBotConfig)
    assert cfg.greeting == "Hi"


def test_union_resolves_echo() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python({"bot_type": "echo", "echo_prefix": ">>"})
    assert isinstance(cfg, EchoBotConfig)
    assert cfg.echo_prefix == ">>"


def test_union_resolves_generic_when_tag_present() -> None:
    cfg = TypeAdapter(BotConfigUnion).validate_python({"bot_type": "generic"})
    assert isinstance(cfg, BotConfigSchema)
    assert cfg.bot_type is BotType.GENERIC


def test_union_requires_discriminator() -> None:
    # The strict discriminated union rejects an untagged dict — the tolerant
    # ``parse_bot_config`` (below) is what falls back to generic for legacy rows.
    with pytest.raises(ValidationError):
        TypeAdapter(BotConfigUnion).validate_python({})


def test_union_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BotConfigUnion).validate_python({"bot_type": "alien"})


# ---- tolerant parse_bot_config ----
def test_parse_hello() -> None:
    assert isinstance(parse_bot_config({"bot_type": "hello", "greeting": "Yo"}), HelloBotConfig)


def test_parse_empty_falls_back_to_generic() -> None:
    cfg = parse_bot_config({})
    assert isinstance(cfg, BotConfigSchema)
    assert cfg.bot_type is BotType.GENERIC


def test_parse_legacy_missing_bot_type_falls_back_to_generic() -> None:
    cfg = parse_bot_config({"welcome_message": "legacy"})
    assert isinstance(cfg, BotConfigSchema)
    assert cfg.bot_type is BotType.GENERIC


def test_parse_unknown_bot_type_falls_back_to_generic() -> None:
    cfg = parse_bot_config({"bot_type": "unknown", "welcome_message": "w"})
    assert isinstance(cfg, BotConfigSchema)
    assert cfg.bot_type is BotType.GENERIC


# ---- per-type default helpers ----
def test_default_config_for_generic() -> None:
    cfg = _default_config_for(BotType.GENERIC)
    assert isinstance(cfg, BotConfigSchema)
    assert cfg.bot_type is BotType.GENERIC


def test_default_config_for_hello() -> None:
    cfg = _default_config_for(BotType.HELLO)
    assert isinstance(cfg, HelloBotConfig)
    assert cfg.greeting


def test_default_config_for_echo() -> None:
    cfg = _default_config_for(BotType.ECHO)
    assert isinstance(cfg, EchoBotConfig)
    assert cfg.echo_prefix

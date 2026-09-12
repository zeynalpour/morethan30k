"""Tests for S2.1 — the template registry core.

Pure unit tests: they run with **no Postgres and no Redis** (an acceptance
criterion — the registry is in-code data importable with zero
infrastructure; the conftest placeholders never open connections). Covers
union validation of every shipped seed, the ``TemplateSpec`` invariants,
lookup/version resolution, provenance stamping, the i18n constraints, and
the no-divergence pin between the registry's scratch seeds and
``_default_config_for`` (the cache-priming regression precedent).
"""

from __future__ import annotations

from collections.abc import Iterator
import re

from pydantic import TypeAdapter, ValidationError
import pytest

from tme.core.i18n import localize
from tme.database.models import BotType
from tme.schemas.bot_config import (
    BotConfigSchema,
    BotConfigUnion,
    HelloBotConfig,
    parse_bot_config,
)
from tme.services.managed_bots import _default_config_for
from tme.templates import TemplateSpec, default_seed_for, get_template, list_templates, register
from tme.templates.builtin import HELLO_WORLD_SPEC
from tme.templates.registry import REGISTRY

#: The S2.1 launch set, in registration (picker-card) order.
TEMPLATE_IDS = ["hello_world", "echo", "feedback_collector", "quiz", "simple_form"]

_CONVERSATIONAL_IDS = ["feedback_collector", "quiz", "simple_form"]

_union_adapter = TypeAdapter(BotConfigUnion)


@pytest.fixture
def pristine_registry() -> Iterator[None]:
    """Snapshot/restore :data:`REGISTRY` so version-mechanics tests can't leak."""
    saved = {template_id: list(specs) for template_id, specs in REGISTRY.items()}
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


# ------------------------------------------------------------ launch set ---
def test_registry_holds_launch_set() -> None:
    """Five templates registered in picker order; no scratch entries listed."""
    assert [spec.id for spec in list_templates()] == TEMPLATE_IDS


def test_every_listed_spec_is_latest_version() -> None:
    for template_id in TEMPLATE_IDS:
        assert get_template(template_id) is REGISTRY[template_id][-1]
    assert list_templates() == [REGISTRY[tid][-1] for tid in REGISTRY]


# ------------------------------------------------- spec validation invariants
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seed_validates_through_union(template_id: str) -> None:
    """Every seed is a config the flow engine accepts — revalidated raw."""
    spec = get_template(template_id)
    revalidated = _union_adapter.validate_python(spec.seed.model_dump())
    assert revalidated.model_dump() == spec.seed.model_dump()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seed_round_trips_through_parse_bot_config(template_id: str) -> None:
    """The exact write-path parse (union, with the tolerant fallback) is lossless."""
    spec = get_template(template_id)
    assert parse_bot_config(spec.seed.model_dump()).model_dump() == spec.seed.model_dump()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seed_bot_type_matches_spec(template_id: str) -> None:
    assert get_template(template_id).seed.bot_type == get_template(template_id).bot_type


def test_spec_rejects_type_mismatch_between_seed_and_column() -> None:
    """A generic seed under a hello spec fails validation, not provisioning."""
    with pytest.raises(ValidationError, match="does not match declared"):
        TemplateSpec(
            id="broken",
            version=1,
            bot_type=BotType.HELLO,
            display_name="Broken",
            description="generic seed mislabelled as hello",
            seed=BotConfigSchema.default(),
        )


def test_spec_rejects_bad_id_slug() -> None:
    with pytest.raises(ValidationError):
        TemplateSpec(
            id="Bad-ID",
            version=1,
            bot_type=BotType.GENERIC,
            display_name="Broken",
            description="ids are ^[a-z0-9_]{2,32}$ slugs",
            seed=BotConfigSchema.default(),
        )


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_ids_fit_slug_regex(template_id: str) -> None:
    assert re.fullmatch(r"[a-z0-9_]{2,32}", template_id)


def test_ids_unique_in_registry() -> None:
    listed = list_templates()
    ids = [spec.id for spec in listed]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------- version resolution ---
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_version_list_sorted_per_id(template_id: str) -> None:
    versions = [spec.version for spec in REGISTRY[template_id]]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def _hello_spec(version: int) -> TemplateSpec:
    """A valid hello_world-style spec at an arbitrary version."""
    dumped = get_template("hello_world").seed.model_dump()
    dumped["template"] = {"id": "hello_world", "version": version}
    return TemplateSpec(
        id="hello_world",
        version=version,
        bot_type=BotType.HELLO,
        display_name="Hello World",
        description="test bump",
        seed=_union_adapter.validate_python(dumped),
    )


def test_get_template_resolves_latest_and_pinned(pristine_registry: None) -> None:
    register(_hello_spec(version=3))
    register(_hello_spec(version=2))
    assert [spec.version for spec in REGISTRY["hello_world"]] == [1, 2, 3]
    assert get_template("hello_world").version == 3  # latest wins
    assert get_template("hello_world", version=1).version == 1  # pinned
    assert get_template("hello_world", version=2).version == 2
    assert get_template("hello_world", version=3).version == 3


def test_register_rejects_duplicate_version(pristine_registry: None) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register(HELLO_WORLD_SPEC)  # (hello_world, v1) is already registered


def test_get_template_unknown_id_raises() -> None:
    with pytest.raises(KeyError, match="unknown template id"):
        get_template("does_not_exist")


def test_get_template_unknown_version_raises() -> None:
    with pytest.raises(KeyError, match="no version 99"):
        get_template("hello_world", version=99)


# ---------------------------------------------------------- provenance ----
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seed_carries_its_own_provenance_stamp(template_id: str) -> None:
    """The stamp records exactly which seed version a clone came from."""
    spec = get_template(template_id)
    assert spec.seed.model_dump()["template"] == {"id": spec.id, "version": spec.version}


def test_scratch_generic_default_is_unstamped() -> None:
    """A from-scratch bot has no provenance — S2.3's adoption path covers it."""
    assert "template" not in default_seed_for(BotType.GENERIC).model_dump()


# ---------------------------------------------------------- default seeds ---
@pytest.mark.parametrize("bot_type", [BotType.GENERIC, BotType.HELLO, BotType.ECHO])
def test_default_seed_no_divergence_from_bare_default(bot_type: BotType) -> None:
    """Registry scratch seeds must equal-or-superset ``_default_config_for``.

    Two sources of defaults diverging is the cache-priming regression
    precedent: a fresh per-type default served a Redis cache contradicting
    the persisted row until the TTL. (S2.3 collapses the two sources.)
    """
    default = _default_config_for(bot_type)
    seed = default_seed_for(bot_type)
    assert type(seed) is type(default)
    for field in default.model_fields_set:
        assert getattr(seed, field) == getattr(default, field), (
            f"{bot_type} default seed diverged on {field!r}"
        )


def test_default_seed_for_generic_is_todays_starter() -> None:
    assert default_seed_for(BotType.GENERIC).model_dump() == BotConfigSchema.default().model_dump()


@pytest.mark.parametrize("bot_type", [BotType.BRIDGE, BotType.AI_GATEWAY])
def test_default_seed_unprovisionable_types_raise(bot_type: BotType) -> None:
    with pytest.raises(KeyError):
        default_seed_for(bot_type)


def test_default_seed_returns_fresh_copy() -> None:
    """Callers may adjust a seed without poisoning the in-code registry."""
    seed = default_seed_for(BotType.HELLO)
    seed.welcome_message = "mutated"
    assert default_seed_for(BotType.HELLO).welcome_message != "mutated"


# ------------------------------------------------------------- i18n rules ---
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_seeds_never_lock_single_language(template_id: str) -> None:
    """single_language is an owner decision; a seed must not pre-lock it."""
    assert get_template(template_id).seed.single_language is False


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_translations_declare_en_base(template_id: str) -> None:
    assert "en" in get_template(template_id).seed.translations


def test_hello_world_ships_fa_welcome_and_fallback() -> None:
    seed = get_template("hello_world").seed
    assert isinstance(seed, HelloBotConfig)
    fa = seed.translations["fa"]
    assert fa.welcome_message
    assert fa.fallback_message


# ------------------------------------------------- engine consumption ----
@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
@pytest.mark.parametrize("language", [None, "en", "fa"])
def test_engine_localizes_every_seed(template_id: str, language: str | None) -> None:
    """The shared engine's copy resolution handles every seed (all chains)."""
    copy = localize(get_template(template_id).seed, language)
    assert copy.welcome_message
    assert copy.fallback_message


@pytest.mark.parametrize("template_id", _CONVERSATIONAL_IDS)
def test_conversational_seeds_are_generic_flows_with_steps(template_id: str) -> None:
    """Feedback/Quiz/Form run as GENERIC flows — no new union variants."""
    seed = get_template(template_id).seed
    assert isinstance(seed, BotConfigSchema)
    assert seed.active_modules == ["steps"]
    dumped = seed.model_dump()
    assert len(dumped["steps"]) >= 2  # every conversational flow gathers ≥1 answer
    assert seed.menu_buttons  # the today-engine path (menu) stays populated too


def test_quiz_steps_carry_correct_answers() -> None:
    steps = get_template("quiz").seed.model_dump()["steps"]
    scored = [step for step in steps if "correct_answers" in step]
    assert len(scored) == 3
    assert all(step["correct_answers"] for step in scored)


def test_feedback_steps_mix_options_and_free_text() -> None:
    steps = get_template("feedback_collector").seed.model_dump()["steps"]
    assert "options" in steps[0]
    assert steps[1]["answer_type"] == "free_text"

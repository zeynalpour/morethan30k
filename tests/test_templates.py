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
from tme.templates import (
    PRESERVABLE_KEYS,
    STAMP_KEY,
    TemplateSpec,
    clone_flow,
    default_seed_for,
    get_template,
    list_templates,
    register,
    resolve_template,
    template_updates,
)
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


# ===================================================== S2.3 — re-clone mechanics
def _bumped_hello(version: int, greeting: str) -> TemplateSpec:
    """A hello_world bump at ``version`` with a changed seed copy."""
    dumped = get_template("hello_world", version=1).seed.model_dump()
    dumped["greeting"] = greeting
    dumped[STAMP_KEY] = {"id": "hello_world", "version": version}
    return TemplateSpec(
        id="hello_world",
        version=version,
        bot_type=BotType.HELLO,
        display_name="Hello World",
        description="test bump",
        seed=_union_adapter.validate_python(dumped),
    )


def test_resolve_template_reads_the_flow_stamp(pristine_registry: None) -> None:
    stamped = get_template("quiz").seed.model_dump()
    assert resolve_template(stamped).id == "quiz"
    assert resolve_template(stamped).version == 1  # the PINNED version…
    register(_hello_spec(version=3))
    assert resolve_template(stamped).version == 1  # …not the latest
    assert resolve_template(get_template("hello_world").seed.model_dump()).version == 3


def test_resolve_template_rejects_unstamped_and_legacy_flows() -> None:
    # Scratch bot (S2.2's unstamped generic default).
    assert "template" not in default_seed_for(BotType.GENERIC).model_dump()
    with pytest.raises(KeyError, match="no template provenance"):
        resolve_template(default_seed_for(BotType.GENERIC).model_dump())
    # Pre-Phase-2 row: a flow dict without any stamp rider.
    legacy = {"bot_type": "generic", "welcome_message": "old starter"}
    with pytest.raises(KeyError, match="no template provenance"):
        resolve_template(legacy)
    # Malformed riders read as no-provenance too (tolerant, never an error
    # at the read path — the dashboard treats it as adoption-ready).
    with pytest.raises(KeyError, match="no template provenance"):
        resolve_template({"template": {"id": "quiz"}})  # version missing
    with pytest.raises(KeyError, match="no template provenance"):
        resolve_template({"template": "quiz"})  # not a dict


def test_resolve_template_unknown_pinned_version_raises(pristine_registry: None) -> None:
    stamped = get_template("quiz").seed.model_dump()
    stamped[STAMP_KEY] = {"id": "quiz", "version": 99}  # version since removed
    with pytest.raises(KeyError):
        resolve_template(stamped)


def test_template_updates_none_when_registry_not_ahead() -> None:
    stamped = get_template("hello_world").seed.model_dump()  # v1 == latest v1
    assert template_updates(stamped) is None


def test_template_updates_flips_when_registry_bumps(pristine_registry: None) -> None:
    stamped_v1 = get_template("hello_world", version=1).seed.model_dump()
    assert template_updates(stamped_v1) is None
    register(_bumped_hello(version=2, greeting="Hi v2! 👋"))
    update = template_updates(stamped_v1)
    assert update is not None
    assert update.version == 2
    assert update.seed.model_dump()[STAMP_KEY] == {"id": "hello_world", "version": 2}
    # The clone at the latest version sees nothing again.
    assert template_updates(update.seed.model_dump()) is None


def test_template_updates_none_for_unstamped_scratch_and_legacy() -> None:
    assert template_updates(default_seed_for(BotType.GENERIC).model_dump()) is None
    assert template_updates({"bot_type": "generic"}) is None
    assert template_updates({"template": {"version": 2}}) is None  # no id → ignored


def test_template_updates_none_when_template_deleted(pristine_registry: None) -> None:
    stamped = get_template("quiz").seed.model_dump()
    del REGISTRY["quiz"]
    assert template_updates(stamped) is None


def test_clone_flow_replaces_base_and_carries_whitelist() -> None:
    old = get_template("hello_world", version=1).seed.model_dump()
    old["greeting"] = "OWNER EDIT"  # base-copy customization…
    old["translations"] = {"en": {"greeting": "owner translation"}}
    old["single_language"] = True  # …and the Phase 1 owner layer
    old["menu_buttons"] = [{"text": "owner button", "callback": "x"}]

    spec = _bumped_hello(version=2, greeting="Fresh v2 copy")
    flow = clone_flow(spec, old, None)  # None → full whitelist

    assert flow["greeting"] == "Fresh v2 copy"  # base replaced wholesale
    assert flow["menu_buttons"] == spec.seed.model_dump()["menu_buttons"]
    assert flow["translations"] == {"en": {"greeting": "owner translation"}}  # carried
    assert flow["single_language"] is True  # owner mode survives
    assert flow[STAMP_KEY] == {"id": "hello_world", "version": 2}  # new stamp


def test_clone_flow_preserve_optout_and_explicit_keys() -> None:
    old = get_template("hello_world", version=1).seed.model_dump()
    old["translations"] = {"fa": {"greeting": "سلام"}}
    old["single_language"] = True

    spec = _bumped_hello(version=2, greeting="v2")
    # Explicit narrow whitelist: translations only.
    flow = clone_flow(spec, old, ["translations"])
    assert flow["translations"] == {"fa": {"greeting": "سلام"}}
    assert flow["single_language"] is False  # the seed's own value — owner opted out
    # Single string accepted as one-key whitelist.
    flow2 = clone_flow(spec, old, "single_language")
    assert flow2["single_language"] is True
    assert flow2["translations"] == spec.seed.model_dump()["translations"]
    # Empty whitelist = a fully bare reset.
    flow3 = clone_flow(spec, old, [])
    assert flow3["translations"] == spec.seed.model_dump()["translations"]
    assert flow3["single_language"] is False


def test_clone_flow_rejects_keys_outside_whitelist() -> None:
    old = get_template("hello_world").seed.model_dump()
    spec = _bumped_hello(version=2, greeting="v2")
    with pytest.raises(ValueError, match="not preservable: menu_buttons"):
        clone_flow(spec, old, ["menu_buttons"])
    with pytest.raises(ValueError, match="not preservable: welcome_message"):
        clone_flow(spec, old, "welcome_message")


def test_clone_flow_without_old_flow_uses_pure_seed() -> None:
    """Adoption on a bot with no config row yet: nothing to carry."""
    spec = _bumped_hello(version=2, greeting="v2")
    flow = clone_flow(spec, {}, None)
    assert flow == spec.seed.model_dump()


def test_clone_flow_result_validates_through_union() -> None:
    """The rebuilt flow (stamp + preserved keys) round-trips the write path.

    Same losslessness shape as the S2.1 seed tests: dumping the parsed
    config equals dumping it again — preserved keys and stamp all survive.
    (``model_dump()`` fills optional translation fields with ``None``, so
    the equality is dump-vs-redump, never raw-dict-vs-dump.)
    """
    old = get_template("quiz").seed.model_dump()
    old["translations"] = {"fa": {"fallback_message": "دوباره امتحان کنید"}}
    old["single_language"] = True
    spec = get_template("quiz")  # any version — the mechanics are identical
    flow = clone_flow(spec, old, None)
    parsed = parse_bot_config(flow)
    assert parsed.single_language is True  # preserved key is typed, not lost
    assert parsed.translations["fa"].fallback_message == "دوباره امتحان کنید"
    redumped = parsed.model_dump()
    assert _union_adapter.validate_python(redumped).model_dump() == redumped
    # The stamp rider survives the union round-trip (extra="allow").
    assert redumped[STAMP_KEY] == {"id": "quiz", "version": spec.version}


def test_preservable_keys_are_exactly_translations_and_single_language() -> None:
    """The whitelist is closed: the Phase 1 i18n layer, nothing else."""
    assert PRESERVABLE_KEYS == ("translations", "single_language")

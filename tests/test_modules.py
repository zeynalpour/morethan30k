"""Tests for the in-code module registry (IDEAS N step 0).

The defect being removed: ``active_modules`` was a free-text dashboard field, so
a bot could **advertise** a module (``steps``) its flow did not contain. The
registry is the closed, in-code list of capabilities that genuinely exist, and
the flag is now DERIVED from the flow on every write — these tests pin both
halves of that contract, plus the invariant that keeps the registry honest:
only real modules, each with its own detector.
"""

from __future__ import annotations

import pytest

from tme.modules import (
    derive_active_modules,
    get_module,
    is_active,
    list_modules,
    normalize_flow,
)
from tme.modules.registry import ModuleSpec, register
from tme.templates import get_template

#: The capabilities the shared engine actually runs today. A new entry here is
#: a claim that the engine reads its config — adding a toggle for a capability
#: that does not exist would repeat exactly the lie this module removes.
REAL_MODULES = ["steps"]


def _steps_flow() -> dict:
    """A flow that genuinely contains the ``steps`` module."""
    return {
        "bot_type": "generic",
        "welcome_message": "Hi",
        "steps": [
            {
                "id": "rating",
                "prompt": "How would you rate us?",
                "options": [{"label": "Good", "value": "good"}],
            },
            {"id": "comment", "prompt": "Anything to add?", "answer_type": "free_text"},
        ],
    }


def _plain_flow() -> dict:
    """A welcome/menu bot — no module config at all."""
    return {"bot_type": "generic", "welcome_message": "Hi"}


# --------------------------------------------------------------------------- #
# The registry itself
# --------------------------------------------------------------------------- #
def test_registry_exposes_only_modules_that_really_exist() -> None:
    """The registry is the closed set of shipped capabilities — exactly."""
    assert [spec.id for spec in list_modules()] == REAL_MODULES
    # Capabilities the engine does NOT implement yet must not be advertised.
    listed = {spec.id for spec in list_modules()}
    assert listed.isdisjoint({"ai_reply", "translate", "rag_answer", "broadcast", "faq"})


@pytest.mark.parametrize("spec", list_modules(), ids=lambda spec: spec.id)
def test_every_registered_module_is_describable_and_detectable(spec: ModuleSpec) -> None:
    """A module is only registerable with a version, copy and owned config keys.

    ``register`` takes the detector as a mandatory keyword argument, so a spec
    without one cannot exist — this pins that the shipped entries carry real
    metadata and that detection is callable for every one of them.
    """
    assert spec.version >= 1
    assert spec.display_name and spec.description
    assert spec.config_keys, "a module must name the flow keys it owns"
    assert is_active(spec.id, _plain_flow()) is False


def test_registry_rejects_duplicates_and_unknown_dependencies() -> None:
    """Both failures raise before anything is mutated (the registry stays clean)."""
    before = [spec.id for spec in list_modules()]

    with pytest.raises(ValueError, match="already registered"):
        register(get_module("steps"), active_when=lambda _flow: True)

    with pytest.raises(ValueError, match="unregistered"):
        register(
            ModuleSpec(
                id="hypothetical",
                version=1,
                display_name="X",
                description="Y",
                dependencies=("nope",),
            ),
            active_when=lambda _flow: True,
        )

    assert [spec.id for spec in list_modules()] == before


def test_unknown_module_lookup_raises() -> None:
    with pytest.raises(KeyError, match="unknown module id"):
        get_module("ai_reply")


# --------------------------------------------------------------------------- #
# Derivation — the flag describes the flow, not the owner's typing
# --------------------------------------------------------------------------- #
def test_steps_flow_derives_the_steps_module() -> None:
    assert derive_active_modules(_steps_flow()) == ["steps"]
    assert is_active("steps", _steps_flow()) is True


def test_flow_without_steps_derives_no_modules() -> None:
    assert derive_active_modules(_plain_flow()) == []
    assert is_active("steps", _plain_flow()) is False
    # An empty/absent steps array is not a module either.
    assert derive_active_modules({"steps": []}) == []
    assert derive_active_modules({"steps": None}) == []
    # Neither is malformed data the engine would drop (no usable step).
    assert derive_active_modules({"steps": "oops"}) == []
    assert derive_active_modules({"steps": [{"nope": "missing required fields"}]}) == []


def test_the_flag_itself_is_never_the_source_of_truth() -> None:
    """Hand-written flags are ignored — only the flow's data counts.

    This is the load-bearing rule of the fix: if the detector trusted
    ``active_modules`` the value could never be derived from it.
    """
    assert derive_active_modules({"active_modules": ["steps"]}) == []
    assert derive_active_modules({"active_modules": ["steps", "ai_reply"]}) == []


@pytest.mark.parametrize("template_id", ["feedback_collector", "quiz", "simple_form"])
def test_shipped_steps_templates_derive_the_steps_module(template_id: str) -> None:
    """Every conversational seed genuinely contains what it advertises."""
    assert derive_active_modules(get_template(template_id).seed.model_dump()) == ["steps"]


@pytest.mark.parametrize("template_id", ["hello_world", "echo"])
def test_shipped_non_steps_templates_derive_nothing(template_id: str) -> None:
    assert derive_active_modules(get_template(template_id).seed.model_dump()) == []


# --------------------------------------------------------------------------- #
# normalize_flow — what every write path persists
# --------------------------------------------------------------------------- #
def test_normalize_drops_a_module_the_flow_does_not_contain() -> None:
    """The exact defect: a bot advertising ``steps`` with no steps in its flow."""
    normalized = normalize_flow({"active_modules": ["steps"], "welcome_message": "Hi"})

    assert normalized["active_modules"] == []
    assert normalized["welcome_message"] == "Hi"  # nothing else touched


def test_normalize_adds_a_module_the_flow_contains_but_does_not_advertise() -> None:
    """The other direction: the flow has steps, so the flag must say so."""
    flow = _steps_flow()
    flow["active_modules"] = []

    assert normalize_flow(flow)["active_modules"] == ["steps"]


def test_normalize_drops_names_the_registry_does_not_know() -> None:
    """Typed junk cannot survive a write — those capabilities do not exist."""
    normalized = normalize_flow(
        {"active_modules": ["ai_reply", "translate"], "welcome_message": "Hi"}
    )

    assert normalized["active_modules"] == []


def test_normalize_always_writes_the_key_and_never_mutates_the_input() -> None:
    flow = _plain_flow()
    normalized = normalize_flow(flow)

    assert normalized["active_modules"] == []  # present, honestly empty
    assert "active_modules" not in flow  # the caller's dict is untouched
    assert normalized is not flow


def test_normalize_is_idempotent() -> None:
    once = normalize_flow(_steps_flow())
    twice = normalize_flow(once)

    assert once == twice

"""Regression pins for the AUTHORING-GATE RULE (IDEAS N step 0).

The rule: a module's derived (or configured) enabled-state may gate EXECUTION
(does the engine run this module for this bot) and may render a read-only
status, but it must NEVER gate AUTHORING — the controls that create or edit
that module's data stay reachable whether the module reads on or off.

The live defect these pin: with zero steps in the flow, ``active_modules``
derived ``steps`` as OFF, and the dashboard conditioned the steps editor (and
its ➕ Add step control) on that same state — so deleting the last step made the
editor disappear and the owner could never author a step again. The flag is a
CONSEQUENCE of the data, so it can never control the tool that produces it.

Why source-level: this repo has no frontend test harness (vite + React, no
vitest/jest — see package.json), so the UI half of the rule is pinned by
asserting the exact anti-pattern is absent from the components and the rule is
marked where a future edit would break it. That is NOT a browser-driven proof
that the control is clickable; the PR says so plainly. The API half (authoring
a step from an empty flow turns the module on, and stays re-authorable after
the flag flips off again) is covered end-to-end in ``tests/test_api.py``
(``test_authoring_a_step_back_from_an_empty_flow_turns_the_module_on``).

If a frontend harness ever lands, replace the pattern assertions below with a
render test: mount ConfigEditor with ``flow.steps == []`` and assert the
➕ Add step control exists and appends a step.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

#: The exact marker a component must carry when it renders a module's state, so
#: the next editor reads the rule before adding a condition.
RULE_MARKER = "AUTHORING-GATE RULE"

_SRC = Path(__file__).resolve().parents[1] / "src"
COMPONENTS = _SRC / "components"

CONFIG_EDITOR = COMPONENTS / "ConfigEditor.tsx"
FLOW_STEPS_EDITOR = COMPONENTS / "FlowStepsEditor.tsx"
MODULES_PANEL = COMPONENTS / "ModulesPanel.tsx"

#: Any of these conditions around the steps editor re-opens the one-way door:
#: they all mean "the editor/the module's authoring control is only reachable
#: when the derived state says so".
FORBIDDEN_GATES = (
    r"\{hasSteps\s*&&",
    r"\{!?hasSteps\s*\?",
    r"\{active_modules[^}\n]*&&",
    r"\{mod\.active\s*&&",
    r"\{!mod\.active\s*\?",
    r"active_modules\.includes",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} moved — these pins must be updated with it"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path", [CONFIG_EDITOR, FLOW_STEPS_EDITOR, MODULES_PANEL], ids=lambda p: p.name
)
def test_derived_module_state_never_gates_an_authoring_control(path: Path) -> None:
    """No component may condition an authoring control on derived state."""
    text = _read(path)
    for pattern in FORBIDDEN_GATES:
        assert not re.search(pattern, text), (
            f"{path.name} gates authoring on derived state ({pattern!r}) — a "
            f"module's flag may gate execution, never authorship"
        )


@pytest.mark.parametrize(
    "path", [CONFIG_EDITOR, FLOW_STEPS_EDITOR, MODULES_PANEL], ids=lambda p: p.name
)
def test_each_component_marks_the_authoring_gate_rule(path: Path) -> None:
    """The rule is written where the next change to this UI will read it."""
    assert RULE_MARKER in _read(path), f"{path.name} lost the {RULE_MARKER} note"


def test_the_steps_editor_and_its_add_step_control_are_always_rendered() -> None:
    """The editor renders unconditionally, and it carries the Add step control.

    ``FlowStepsEditor`` is the component that owns ➕ Add step (the only way to
    create a step), and ``ConfigEditor`` must mount it outside of any condition
    — the pre-fix code wrapped it in ``{hasSteps && (...)}``.
    """
    editor = _read(FLOW_STEPS_EDITOR)
    assert "onClick={addStep}" in editor, "the ➕ Add step control disappeared"
    assert 'data-testid="add-step"' in editor, "the Add step control lost its test hook"

    config = _read(CONFIG_EDITOR)
    assert "<FlowStepsEditor" in config
    # Nothing between the section opening and the editor may be a condition:
    # the editor's own section is the first thing its JSX returns after `{`.
    assert config.index("<FlowStepsEditor") > config.index("AUTHORING IS NEVER GATED")


def test_the_off_state_of_a_module_still_exposes_its_authoring_control() -> None:
    """A module that reads off keeps the control that authors its data.

    ``ModulesPanel`` maps a module id to that control (never to
    ``mod.active``), and renders it for every state — including the off state
    the owner gets after deleting the last step.
    """
    panel = _read(MODULES_PANEL)
    assert "MODULE_AUTHORING" in panel
    assert re.search(r"steps:\s*\{", panel), "the steps module has no authoring control wired"
    assert "authoring_hint" in panel, "the panel ignores the registry's authoring hint"
    assert "onAuthorStep?." in panel, "the wired authoring action is never invoked"
    # The anchor the affordance jumps to is shared, not duplicated per component.
    anchors = (_SRC / "lib" / "ui-anchors.ts").read_text(encoding="utf-8")
    assert "FLOW_STEPS_ANCHOR_ID" in anchors
    assert "id={FLOW_STEPS_ANCHOR_ID}" in _read(CONFIG_EDITOR), (
        "the steps section lost its anchor — the panel's authoring affordance would point nowhere"
    )

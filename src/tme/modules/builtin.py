"""Shipped modules — the capabilities that genuinely exist in the engine today.

Importing this module registers them. The list is deliberately SHORT: a toggle
for a capability the engine does not implement would be the same lie the
registry removes (advertising a module the bot cannot contain), so
``ai_reply``, ``translate``, ``rag_answer``, ``broadcast`` … land here only
when the shared engine actually reads their config. IDEAS N sequences those as
registry follow-ups (they ride Phase 4's gateway, Phase 5's engine, …).

Adding a module = one :func:`~tme.modules.registry.register` call plus its
detector — no handler, no ``BotType`` member, no migration, no frontend change
(the dashboard renders whatever the registry lists).
"""

from __future__ import annotations

from tme.modules.registry import ModuleSpec, register
from tme.services.steps import STEP_MODULE, has_step_data

#: The ``steps`` primitive (Phase 2, S2.2): multi-step conversational flows.
#: The engine already reads this module's config — this entry only gives the
#: capability a name, a version, the keys it owns and a detector, so the
#: dashboard can show the truth instead of a free-text field.
STEPS_SPEC = ModuleSpec(
    id=STEP_MODULE,
    version=1,
    display_name="Flow steps",
    description=(
        "Multi-step conversational flows: the bot asks each step's question, "
        "routes option taps and free-text answers, scores quiz answers and "
        "sends the collected answers to the owner."
    ),
    config_keys=("steps",),
)

register(STEPS_SPEC, active_when=has_step_data)

__all__ = ["STEPS_SPEC"]

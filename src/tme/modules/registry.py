"""The module registry — in-code engine capabilities (IDEAS N step 0).

A *module* is an engine **capability** a bot can have: its own flow config
keys, a declared version, and a detector that answers "does this bot's flow
actually contain it?". It is NOT a whole seed flow (that is a
:class:`~tme.templates.registry.TemplateSpec`) and NOT third-party code — the
boundary that decides viability is *declarative, never in-process code*
(``IDEAS.md`` § N).

Why the registry exists at all: the dashboard used to expose ``active_modules``
as a free-text field, so a bot could **advertise** a module its flow did not
contain. Step 0 of § N removes that lie by making the flag *bookkeeping*: the
registry knows every capability that genuinely exists, and the flag is
**derived** from the flow on every write
(:func:`normalize_flow`) instead of being typed.

The registry is pure in-memory data — no Postgres, no Redis, no migration.
The ``modules``/``module_versions``/``bot_modules`` tables arrive with the
store (IDEAS N step 3); until then the in-code registry is the source of
truth (see ``docs/architecture/01-data-model.md``).

Nothing here adds a handler or a ``BotType`` member: a module is config the
one shared engine already understands (north star: no per-bot Python).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "MODULE_REGISTRY",
    "Flow",
    "ModuleSpec",
    "derive_active_modules",
    "get_module",
    "is_active",
    "list_modules",
    "normalize_flow",
    "register",
]

#: A raw flow dict as stored in ``bot_configs.flow``.
Flow = Mapping[str, Any]

#: A module's detector: "does this flow contain what the module needs?".
#: It reads the flow's DATA only — never ``active_modules`` (the value being
#: derived; reading it would be circular and would resurrect the old lie).
Detector = Callable[[Flow], bool]


class ModuleSpec(BaseModel):
    """One registered engine capability.

    Attributes:
        id: Slug identifying the module (``^[a-z0-9_]{2,32}$``, matching the
            template-slug shape). This is the ``active_modules`` string.
        version: Module revision — bumped when the capability's behaviour or
            its config keys change. The store pins per-bot versions later;
            today every bot runs the latest (and only) version.
        display_name: English base name for the dashboard toggle.
        description: English one-liner: what the capability does, and which
            config it needs, so the toggle is never a bare label.
        authoring_hint: How the owner turns this module ON — where its data is
            authored. Mandatory, because a module whose authoring path is not
            named is a one-way door: with its data absent the derived flag
            reads "off", and if nothing says how to create that data the owner
            can never get it back. The dashboard renders this beside a module
            that reads "off" together with that module's authoring control
            (a derived flag may gate execution, never authorship).
        config_keys: Flow keys the module owns (``steps`` for the steps
            module). These are the keys the detector looks at.
        dependencies: Other module ids this one needs. Registration order is
            enforced, so a dependency is always registered first (the DAG is
            validated at import time, never discovered at runtime).
    """

    id: str = Field(..., pattern=r"^[a-z0-9_]{2,32}$", description="Module slug.")
    version: int = Field(..., ge=1, description="Module revision.")
    display_name: str = Field(..., min_length=1, max_length=64, description="Toggle label.")
    description: str = Field(
        ..., min_length=1, max_length=280, description="What the capability does."
    )
    authoring_hint: str = Field(
        ...,
        min_length=1,
        max_length=280,
        description="Where/to how the owner authors this module's data (never gated).",
    )
    config_keys: tuple[str, ...] = Field(
        default=(), description="Flow keys this module owns/reads."
    )
    dependencies: tuple[str, ...] = Field(
        default=(), description="Module ids that must be enabled too."
    )


#: The registry proper: module id → spec, in registration (dashboard) order.
#: Populated at import time by :mod:`tme.modules.builtin`. Pure in-memory data.
MODULE_REGISTRY: dict[str, ModuleSpec] = {}

#: module id → detector. Kept beside the registry (not on the spec) so a spec
#: stays plain serializable data — the API serves specs straight to the
#: dashboard, and a callable field would have to be stripped there.
_DETECTORS: dict[str, Detector] = {}


def register(spec: ModuleSpec, *, active_when: Detector) -> None:
    """Add a module to the registry.

    ``active_when`` is mandatory: a module without a way to detect itself in a
    flow is exactly the "advertised but not contained" defect this registry
    exists to remove, so it cannot be registered at all.

    Raises:
        ValueError: The id is already registered, or a declared dependency has
            not been registered yet (keeps the dependency graph acyclic and
            registered in dependency order).
    """
    if spec.id in MODULE_REGISTRY:
        raise ValueError(f"module {spec.id!r} is already registered")
    missing = [dep for dep in spec.dependencies if dep not in MODULE_REGISTRY]
    if missing:
        raise ValueError(
            f"module {spec.id!r} depends on unregistered module(s): {', '.join(sorted(missing))}"
        )
    MODULE_REGISTRY[spec.id] = spec
    _DETECTORS[spec.id] = active_when


def list_modules() -> list[ModuleSpec]:
    """Every registered module, in registration order (dashboard source)."""
    return list(MODULE_REGISTRY.values())


def get_module(module_id: str) -> ModuleSpec:
    """Look up a module by id.

    Raises:
        KeyError: Unknown module id.
    """
    try:
        return MODULE_REGISTRY[module_id]
    except KeyError:
        raise KeyError(f"unknown module id {module_id!r}") from None


def is_active(module_id: str, flow: Flow) -> bool:
    """True when ``flow`` genuinely contains ``module_id``'s capability.

    Derived from the flow's DATA — the stored ``active_modules`` flag is
    deliberately ignored (it is the value this function computes).
    """
    detector = _DETECTORS[get_module(module_id).id]
    return detector(flow)


def derive_active_modules(flow: Flow) -> list[str]:
    """The module ids ``flow`` actually contains, in registry order.

    This is the ONE source of the ``active_modules`` value: a capability is
    listed exactly when its detector finds it in the flow, so a module can
    neither be advertised without being present nor silently dropped while
    present. Unknown names (hand-typed junk, a module removed from the
    registry) simply cannot appear — they are not in the registry.
    """
    return [spec.id for spec in list_modules() if _DETECTORS[spec.id](flow)]


def normalize_flow(flow: Flow) -> dict[str, Any]:
    """Return ``flow`` with ``active_modules`` re-derived from its own content.

    Every flow write path (provisioning, template re-clone, dashboard save,
    bot-type reset) persists the result of this function, so the flag always
    describes the flow it sits in. The keys the *engine* reads are untouched —
    only the bookkeeping flag is rewritten.

    The key is always written (an empty list is the honest answer for a flow
    with no modules) — never removed, so the engine's
    ``flow.get("active_modules") or []`` gate keeps reading the same shape.
    """
    normalized = dict(flow)
    normalized["active_modules"] = derive_active_modules(flow)
    return normalized

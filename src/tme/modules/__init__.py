"""Module library — the engine capabilities a bot can contain (IDEAS N step 0).

Importing this package loads :mod:`tme.modules.builtin`, which registers every
shipped module and its detector. The registry is pure in-memory data: it needs
no Postgres, no Redis, and adds no handler or ``BotType`` member — a module is
config the one shared engine already understands.

Why it exists: ``active_modules`` used to be a free-text dashboard field, so a
bot could advertise a module (``steps``) its flow did not contain. The flag is
now **derived** from the flow by :func:`normalize_flow` on every write, and the
registry is the closed list of capabilities that genuinely exist.

Public surface (re-exports from :mod:`tme.modules.registry`):
:class:`ModuleSpec`, :func:`list_modules`, :func:`get_module`,
:func:`is_active`, :func:`derive_active_modules`, :func:`normalize_flow`,
:func:`register`.
"""

from __future__ import annotations

from tme.modules import builtin as _builtin  # noqa: F401  (registration side-effect)
from tme.modules.registry import (
    MODULE_REGISTRY,
    ModuleSpec,
    derive_active_modules,
    get_module,
    is_active,
    list_modules,
    normalize_flow,
    register,
)

__all__ = [
    "MODULE_REGISTRY",
    "ModuleSpec",
    "derive_active_modules",
    "get_module",
    "is_active",
    "list_modules",
    "normalize_flow",
    "register",
]

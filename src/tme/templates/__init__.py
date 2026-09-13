"""Template library — versioned, in-code ``BotConfig`` seeds (Phase 2).

Importing this package loads :mod:`tme.templates.builtin`, which registers
every shipped seed and validates it against
:class:`~tme.schemas.bot_config.BotConfigUnion` at import time — a broken
template fails at import, never at a live user's provision. The registry is
pure in-memory data: it needs no Postgres, no Redis, and adds no handler or
``BotType`` member (S2.1 north-star: a bot made from a template is an
ordinary ``bot_configs`` row behind the one shared engine).

Public surface (all re-exports from :mod:`tme.templates.registry`):
:class:`TemplateSpec`, :func:`get_template`, :func:`list_templates`,
:func:`resolve_template`, :func:`template_updates`, :func:`clone_flow`,
:func:`stamp_of`.
"""

from __future__ import annotations

from tme.templates import builtin as _builtin  # noqa: F401  (registration side-effect)
from tme.templates.registry import (
    PRESERVABLE_KEYS,
    STAMP_KEY,
    TemplateSpec,
    clone_flow,
    default_seed_for,
    get_template,
    list_templates,
    register,
    resolve_template,
    set_default_seed,
    stamp_of,
    template_updates,
)

__all__ = [
    "PRESERVABLE_KEYS",
    "STAMP_KEY",
    "TemplateSpec",
    "clone_flow",
    "default_seed_for",
    "get_template",
    "list_templates",
    "register",
    "resolve_template",
    "set_default_seed",
    "stamp_of",
    "template_updates",
]

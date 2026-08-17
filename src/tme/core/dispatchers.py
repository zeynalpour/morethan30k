"""Dispatcher assembly.

Two dispatchers, one shared FSM storage:

* ``main_dp``   — handlers for the controller bot only.
* ``tenant_dp`` — the single dispatcher shared by *all* cloned bots. The
  :class:`ConfigMiddleware` runs on it so every tenant update arrives with its
  bot-specific config already loaded from Redis.

Keeping them separate guarantees management commands can never fire inside a
tenant bot, and vice-versa.
"""

from __future__ import annotations

from aiogram import Dispatcher

from tme.core.storage import storage
from tme.middlewares.config_middleware import ConfigMiddleware
from tme.routers.dynamic import dynamic_router
from tme.routers.main_bot import main_router


def build_main_dispatcher() -> Dispatcher:
    """Dispatcher for the controller bot."""
    dp = Dispatcher(storage=storage)
    dp.include_router(main_router)
    return dp


def build_tenant_dispatcher() -> Dispatcher:
    """Dispatcher shared by every tenant bot, with config injection."""
    dp = Dispatcher(storage=storage)
    # Register on the update observer so it runs once per update, ahead of
    # message/callback filtering.
    dp.update.outer_middleware(ConfigMiddleware())
    dp.include_router(dynamic_router)
    return dp


# Built once at import time and reused for the process lifetime.
main_dp: Dispatcher = build_main_dispatcher()
tenant_dp: Dispatcher = build_tenant_dispatcher()

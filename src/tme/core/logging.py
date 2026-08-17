"""Minimal structured logging setup shared across the app."""

from __future__ import annotations

import logging

from tme.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Configure root logging once, idempotently."""
    global _CONFIGURED  # noqa: PLW0603 - module-level init guard, set exactly once
    if _CONFIGURED:
        return

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # aiohttp access logs from aiogram's client are noisy at INFO — tone down.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)

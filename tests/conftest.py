"""Pytest bootstrap.

Sets dummy configuration in the environment *before* any ``tme`` module is
imported, because :mod:`tme.config` builds its settings singleton at import
time. ``setdefault`` is used so a real CI environment (which injects a Postgres
service URL, etc.) always wins over these placeholders.
"""

from __future__ import annotations

import os
import pathlib

os.environ.setdefault("MAIN_BOT_TOKEN", "123456:TEST-CONTROLLER-TOKEN")
os.environ.setdefault("WEBHOOK_BASE_URL", "https://test.example.com")
os.environ.setdefault("WEBHOOK_SECRET", "unit-test-secret-0123456789")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://tme:tme@localhost:5432/tme")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Make the dev box's real .env visible to plain `uv run pytest` runs (the dev
# stack's secrets live in .env; CI injects its own values via workflow env).
# Only vars NOT already set in the process environment are loaded, and only
# from a real .env (never .env.example). APP_ENV stays unset here on purpose:
# the E2E guard defaults unset → skip, so CI/other boxes never run the userbot.
_env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
if _env_file.is_file():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key, _val)

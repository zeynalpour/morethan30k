"""Pytest bootstrap.

Sets dummy configuration in the environment *before* any ``tme`` module is
imported, because :mod:`tme.config` builds its settings singleton at import
time. ``setdefault`` is used so a real CI environment (which injects a Postgres
service URL, etc.) always wins over these placeholders.
"""

from __future__ import annotations

import os

os.environ.setdefault("MAIN_BOT_TOKEN", "123456:TEST-CONTROLLER-TOKEN")
os.environ.setdefault("WEBHOOK_BASE_URL", "https://test.example.com")
os.environ.setdefault("WEBHOOK_SECRET", "unit-test-secret-0123456789")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://tme:tme@localhost:5432/tme")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

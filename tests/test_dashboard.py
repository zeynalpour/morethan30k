"""Tests for dashboard auth-token issuance and validation (S0.4)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace

from tme.services import dashboard


class _FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


def _install(monkeypatch, *, bot_row, issued: list | None = None) -> None:
    """Wire a fake session_scope whose execute returns ``bot_row``."""

    @asynccontextmanager
    async def scope():
        yield _FakeSession(bot_row, issued)

    monkeypatch.setattr(dashboard, "session_scope", scope)


class _FakeSession:
    def __init__(self, bot_row, issued: list | None) -> None:
        self._bot_row = bot_row
        self._issued = issued

    async def execute(self, _statement):
        return _FakeResult(self._bot_row)

    def add(self, obj) -> None:
        if self._issued is not None:
            self._issued.append(obj)


def test_create_token_for_owner(monkeypatch) -> None:
    issued: list = []
    _install(monkeypatch, bot_row=SimpleNamespace(id=7), issued=issued)

    token = asyncio.run(dashboard.create_dashboard_token_for_owner(bot_id=7, owner_telegram_id=42))

    assert token and len(token) >= 32
    assert len(issued) == 1
    row = issued[0]
    assert row.bot_id == 7
    assert row.owner_telegram_id == 42
    # Only the SHA-256 hash is stored — never the raw token.
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.expires_at > datetime.now(UTC)
    assert row.used_at is None


def test_create_token_rejected_for_non_owner(monkeypatch) -> None:
    issued: list = []
    _install(monkeypatch, bot_row=None, issued=issued)

    token = asyncio.run(dashboard.create_dashboard_token_for_owner(bot_id=7, owner_telegram_id=42))

    assert token is None
    assert issued == []


def test_validate_token_ok_and_records_first_use(monkeypatch) -> None:
    row = SimpleNamespace(
        bot_id=7,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        used_at=None,
    )
    _install(monkeypatch, bot_row=row)

    result = asyncio.run(dashboard.validate_dashboard_token("whatever-token"))

    assert result is row
    assert row.used_at is not None  # first use stamped


def test_validate_token_expired(monkeypatch) -> None:
    row = SimpleNamespace(
        bot_id=7,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        used_at=None,
    )
    _install(monkeypatch, bot_row=row)

    result = asyncio.run(dashboard.validate_dashboard_token("whatever-token"))

    assert result is None


def test_validate_token_unknown(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)

    result = asyncio.run(dashboard.validate_dashboard_token("whatever-token"))

    assert result is None

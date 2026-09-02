"""Tests for the managed-bot webhook registration retry/verification logic.

``register_webhook`` turns a freshly-created managed bot into a live tenant.
It must retry through Telegram-side eventual consistency, verify the URL
actually stuck via ``getWebhookInfo``, and never claim success otherwise.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

import tme.services.managed_bots as svc

TOKEN = "123456789:FAKE_TOKEN"
TARGET_URL = f"https://test.example.com/webhook/{TOKEN}"


class _FakeWebhookInfo:
    """Stand-in for the subset of aiogram WebhookInfo that we read."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.last_error_message = None


def _make_session_scope(records: list):
    """Return an async ``session_scope`` that records webhook_registered writes."""

    @asynccontextmanager
    async def scope():
        yield _FakeSession(records)

    return scope


class _FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, records: list) -> None:
        self._records = records

    async def execute(self, _statement):
        return _FakeResult(_FakeRow(self._records))


class _FakeRow:
    def __init__(self, records: list) -> None:
        self._records = records
        self._registered = False

    @property
    def webhook_registered(self):
        return self._registered

    @webhook_registered.setter
    def webhook_registered(self, value):
        self._registered = value
        self._records.append(value)


def _install(monkeypatch, bot: AsyncMock, records: list) -> None:
    """Wire the fake bot, a recording session scope, and fast backoff."""
    monkeypatch.setattr(svc, "get_tenant_bot", lambda _token: bot)
    monkeypatch.setattr(svc, "session_scope", _make_session_scope(records))
    monkeypatch.setattr(svc, "_WEBHOOK_RETRY_BASE_DELAY", 0.001)


def _make_bot(*, info_url: str, set_side_effect=None) -> AsyncMock:
    bot = AsyncMock()
    bot.get_webhook_info.return_value = _FakeWebhookInfo(info_url)
    bot.set_webhook.side_effect = set_side_effect
    return bot


def test_register_webhook_succeeds_and_verifies(monkeypatch) -> None:
    """On verification match it returns True and sets the registered flag."""
    records: list = []
    bot = _make_bot(info_url=TARGET_URL)
    _install(monkeypatch, bot, records)

    result = asyncio.run(svc.register_webhook(TOKEN))

    assert result is True
    assert bot.set_webhook.await_count == 1
    assert bot.get_webhook_info.await_count == 1
    assert records == [True]


def test_register_webhook_retries_on_verification_mismatch(monkeypatch) -> None:
    """A persistent URL mismatch is retried up to the limit, then returns False."""
    records: list = []
    bot = _make_bot(info_url="")  # never matches TARGET_URL
    _install(monkeypatch, bot, records)
    monkeypatch.setattr(svc, "_WEBHOOK_REGISTER_ATTEMPTS", 3)

    result = asyncio.run(svc.register_webhook(TOKEN))

    assert result is False
    assert bot.set_webhook.await_count == 3
    assert bot.get_webhook_info.await_count == 3
    assert records == []  # flag only set on verified success


def test_register_webhook_retries_on_api_error(monkeypatch) -> None:
    """A TelegramBadRequest on set_webhook is retried up to the limit."""
    records: list = []
    bot = _make_bot(
        info_url=TARGET_URL,
        set_side_effect=TelegramBadRequest(method=None, message="boom"),
    )
    _install(monkeypatch, bot, records)
    monkeypatch.setattr(svc, "_WEBHOOK_REGISTER_ATTEMPTS", 2)

    result = asyncio.run(svc.register_webhook(TOKEN))

    assert result is False
    assert bot.set_webhook.await_count == 2
    assert records == []

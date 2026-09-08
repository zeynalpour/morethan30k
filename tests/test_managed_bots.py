"""Tests for the managed-bot webhook registration retry/verification logic.

``register_webhook`` turns a freshly-created managed bot into a live tenant.
It must retry through Telegram-side eventual consistency, verify the URL
actually stuck via ``getWebhookInfo``, and never claim success otherwise.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from tme.config import settings
from tme.database.models import BotType
from tme.schemas.bot_config import BotConfigSchema, EchoBotConfig
import tme.services.managed_bots as svc

TOKEN = "123456789:FAKE_TOKEN"
# Derived from settings so the test matches whatever WEBHOOK_BASE_URL the
# environment provides (CI injects its own value; setdefault must not win).
TARGET_URL = settings.webhook_url_for(TOKEN)


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


def test_provision_primes_cache_with_persisted_config(monkeypatch) -> None:
    """Re-provisioning must cache the persisted config, not a fresh per-type default.

    Regression: the cache was primed with ``_default_config_for(bot_type)`` even
    when an existing row was updated in place, so a re-provisioned bot (whose
    stored config may be user-customized, or of a different type than this
    call's ``bot_type``) could serve behaviour contradicting the DB until the
    cache TTL expired.
    """
    persisted_flow = {"bot_type": "generic", "welcome_message": "user-customized"}
    row = SimpleNamespace(
        token=TOKEN,
        telegram_bot_id=123456789,
        username="custombot",
        title="Custom Bot",
        owner_id=1,
        is_active=True,
        bot_type=BotType.GENERIC,
        config=SimpleNamespace(flow=persisted_flow),
    )

    calls = {"n": 0}

    class _Session:
        async def execute(self, _statement):
            calls["n"] += 1
            # 1st call = owner lookup (exists), 2nd = bot lookup (existing row).
            return _FakeResult(SimpleNamespace(id=1) if calls["n"] == 1 else row)

        async def flush(self):
            return None

    @asynccontextmanager
    async def scope():
        yield _Session()

    cached: list = []
    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username="custombot", full_name="Custom Bot")

    async def fake_set(_token, config):
        cached.append(config)

    monkeypatch.setattr(svc, "get_tenant_bot", lambda _token: bot)
    monkeypatch.setattr(svc, "session_scope", scope)
    monkeypatch.setattr(svc, "register_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "set_bot_config", fake_set)

    asyncio.run(
        svc.provision_managed_bot(
            token=TOKEN,
            owner_telegram_id=42,
            bot_type=BotType.ECHO,  # must NOT clobber the persisted generic config
        )
    )

    assert len(cached) == 1
    cfg = cached[0]
    assert isinstance(cfg, BotConfigSchema)
    assert not isinstance(cfg, EchoBotConfig)
    assert cfg.bot_type is BotType.GENERIC
    assert cfg.welcome_message == "user-customized"


def test_provision_new_bot_primes_cache_with_type_default(monkeypatch) -> None:
    """A brand-new bot gets its per-type default config primed into the cache."""
    cached: list = []

    class _Session:
        async def execute(self, _statement):
            # owner lookup → None (create), bot lookup → None (create).
            return _FakeResult(None)

        def add(self, obj):
            self._added = obj

        async def flush(self):
            if getattr(self, "_added", None) is not None and self._added.id is None:
                self._added.id = 1  # mimic the real flush: assign the surrogate PK

    @asynccontextmanager
    async def scope():
        yield _Session()

    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username="newbot", full_name="New Bot")

    async def fake_set(_token, config):
        cached.append(config)

    monkeypatch.setattr(svc, "get_tenant_bot", lambda _token: bot)
    monkeypatch.setattr(svc, "session_scope", scope)
    monkeypatch.setattr(svc, "register_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "set_bot_config", fake_set)

    asyncio.run(
        svc.provision_managed_bot(
            token=TOKEN,
            owner_telegram_id=42,
            bot_type=BotType.ECHO,
        )
    )

    assert len(cached) == 1
    cfg = cached[0]
    assert isinstance(cfg, EchoBotConfig)
    assert cfg.echo_prefix == "🔁 "

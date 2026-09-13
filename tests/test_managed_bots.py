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
import pytest

from tme.config import settings
from tme.database.models import Bot as BotModel, BotConfig, BotType
from tme.schemas.bot_config import BotConfigSchema, EchoBotConfig
import tme.services.managed_bots as svc
from tme.templates import get_template

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


def test_provision_with_seed_honors_flow_type_and_stamp(monkeypatch) -> None:
    """S2.2: a seed replaces the bare default; row type follows the seed.

    Picking the quiz template must persist exactly its stamped generic flow
    (``active_modules: ["steps"]`` + the S2.1 provenance rider) and set the
    row's ``bot_type`` from the seed's own discriminator — never from the
    call's ``bot_type`` default.
    """
    flows: list = []
    added: list = []

    class _Session:
        async def execute(self, _statement):
            # owner lookup → None (create), bot lookup → None (create).
            return _FakeResult(None)

        def add(self, obj):
            added.append(obj)

        async def flush(self):
            if added and getattr(added[-1], "id", None) is None:
                added[-1].id = 1  # mimic the real flush: assign the surrogate PK

    @asynccontextmanager
    async def scope():
        yield _Session()

    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username="quizbot", full_name="Quiz Bot")

    async def fake_set(_token, config):
        flows.append(config)

    monkeypatch.setattr(svc, "get_tenant_bot", lambda _token: bot)
    monkeypatch.setattr(svc, "session_scope", scope)
    monkeypatch.setattr(svc, "register_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "set_bot_config", fake_set)

    quiz_seed = get_template("quiz").seed  # the registry's stamped flow
    asyncio.run(
        svc.provision_managed_bot(
            token=TOKEN,
            owner_telegram_id=42,
            seed=quiz_seed,  # bot_type deliberately left at its GENERIC default
        )
    )

    # The persisted row's type comes from the seed's discriminator…
    bot_row = next(o for o in added if isinstance(o, BotModel))
    assert bot_row.bot_type is BotType.GENERIC
    # …and the flow seeded into the row is exactly the template's stamped dump.
    assert bot_row.config.flow == quiz_seed.model_dump()
    assert bot_row.config.flow["template"] == {"id": "quiz", "version": 1}

    # Cache primed with exactly what was persisted (parse of the row's flow).
    assert len(flows) == 1
    assert isinstance(flows[0], BotConfigSchema)
    assert flows[0].active_modules == ["steps"]
    assert flows[0].model_dump()["template"] == {"id": "quiz", "version": 1}


def test_provision_seed_overrides_mismatched_bot_type_param(monkeypatch) -> None:
    """A seed's discriminator wins even when ``bot_type`` contradicts it.

    The controller handler passes no ``bot_type`` (generic default), but any
    legacy caller that does pass one must not corrupt the row/flow invariant:
    an echo seed on a hello ``bot_type`` persists an echo row.
    """
    added: list = []

    class _Session:
        async def execute(self, _statement):
            return _FakeResult(None)

        def add(self, obj):
            added.append(obj)

        async def flush(self):
            if added and getattr(added[-1], "id", None) is None:
                added[-1].id = 1

    @asynccontextmanager
    async def scope():
        yield _Session()

    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username="echobot", full_name="Echo Bot")

    monkeypatch.setattr(svc, "get_tenant_bot", lambda _token: bot)
    monkeypatch.setattr(svc, "session_scope", scope)
    monkeypatch.setattr(svc, "register_webhook", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "set_bot_config", AsyncMock())

    echo_seed = get_template("echo").seed
    asyncio.run(
        svc.provision_managed_bot(
            token=TOKEN,
            owner_telegram_id=42,
            bot_type=BotType.HELLO,  # contradicts the seed on purpose
            seed=echo_seed,
        )
    )

    bot_row = next(o for o in added if isinstance(o, BotModel))
    assert bot_row.bot_type is BotType.ECHO  # the seed wins
    assert bot_row.config.flow["bot_type"] == "echo"


# ============================================================== S2.3 re-clone
def _hello_bot_row(flow: dict) -> BotModel:
    """A persisted-shape HELLO bot row carrying ``flow`` (a clone's state)."""
    row = BotModel(
        token=TOKEN,
        token_hash="irrelevant",
        telegram_bot_id=123456789,
        username="clonedbot",
        title="Cloned Bot",
        owner_id=1,
        is_active=True,
        bot_type=BotType.HELLO,
    )
    row.config = BotConfig(flow=flow)
    row.id = 7
    return row


def _install_reclone(monkeypatch) -> tuple[list, AsyncMock]:
    """Fake ``session_scope`` (merge = the same row) + capture invalidations."""
    merged: list = []

    class _Session:
        async def merge(self, obj):
            merged.append(obj)
            return obj

        async def flush(self):
            return None

    @asynccontextmanager
    async def scope():
        yield _Session()

    monkeypatch.setattr(svc, "session_scope", scope)
    invalidate = AsyncMock()
    monkeypatch.setattr(svc, "invalidate_bot_config", invalidate)
    return merged, invalidate


def test_reclone_replaces_base_preserves_owner_layer_and_bumps_stamp(monkeypatch) -> None:
    """The S2.3 contract in one test: fresh base, kept translations + mode,
    new stamp, same bot_type, invalidated cache."""
    old = get_template("hello_world", version=1).seed.model_dump()
    old["greeting"] = "OWNER EDIT"  # base customization — must be REPLACED
    old["translations"] = {"fa": {"greeting": "سلام وکیوم"}}  # owner layer — kept
    old["single_language"] = True  # owner mode — kept
    bot = _hello_bot_row(old)

    # Simulate the registry having moved on: clone to the (bumped) latest.
    merged, _invalidate = _install_reclone(monkeypatch)
    spec = get_template("hello_world")  # latest — same v1 in today's registry
    asyncio.run(svc.reclone_bot(bot, spec))

    assert len(merged) == 1
    row = merged[0]
    assert row is bot
    flow = row.config.flow
    assert flow["greeting"] == "Hello there! 👋"  # owner edit replaced by the seed
    assert flow["translations"] == {"fa": {"greeting": "سلام وکیوم"}}  # carried verbatim
    assert flow["single_language"] is True  # owner mode survives
    assert flow["template"] == {"id": "hello_world", "version": spec.version}
    assert row.bot_type is BotType.HELLO  # unchanged, same-bot_type re-clone


def test_reclone_invalidates_redis_cache(monkeypatch) -> None:
    """Re-clone rides the exact same cache path as every settings write."""
    bot = _hello_bot_row(get_template("hello_world").seed.model_dump())
    _merged, invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, get_template("hello_world")))

    invalidate.assert_awaited_once_with(TOKEN)  # not set_bot_config — invalidate


def test_reclone_idempotent_same_flow_twice(monkeypatch) -> None:
    """Re-cloning the same version twice persists the same flow."""
    spec = get_template("hello_world")
    bot = _hello_bot_row(spec.seed.model_dump())
    merged, _invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, spec))
    first = dict(merged[0].config.flow)
    merged.clear()
    asyncio.run(svc.reclone_bot(bot, spec))
    second = dict(merged[0].config.flow)

    assert first == second


def test_reclone_preserve_optout_drops_owner_layer(monkeypatch) -> None:
    """``preserve=[]`` = a fully bare reset (owner explicitly opted out)."""
    old = get_template("hello_world").seed.model_dump()
    old["translations"] = {"fa": {"greeting": "سلام"}}
    old["single_language"] = True
    bot = _hello_bot_row(old)
    merged, _invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, get_template("hello_world"), preserve=[]))

    flow = merged[0].config.flow
    assert flow["translations"] == get_template("hello_world").seed.model_dump()["translations"]
    assert flow["single_language"] is False


def test_reclone_rejects_keys_outside_whitelist(monkeypatch) -> None:
    bot = _hello_bot_row(get_template("hello_world").seed.model_dump())
    _merged, invalidate = _install_reclone(monkeypatch)

    with pytest.raises(ValueError, match="not preservable"):
        asyncio.run(svc.reclone_bot(bot, get_template("hello_world"), preserve=["greeting"]))
    invalidate.assert_not_awaited()  # nothing persisted, nothing invalidated


def test_reclone_adoption_from_legacy_unstamped_flow(monkeypatch) -> None:
    """A pre-Phase-2 flow (no stamp) adopts the template cleanly."""
    legacy = {"bot_type": "hello", "greeting": "old starter", "single_language": True}
    bot = _hello_bot_row(legacy)
    merged, invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, get_template("hello_world")))

    flow = merged[0].config.flow
    assert flow["template"] == {"id": "hello_world", "version": 1}  # adopted
    assert flow["single_language"] is True  # still the owner's mode
    invalidate.assert_awaited_once_with(TOKEN)


def test_reclone_flips_row_type_with_seed(monkeypatch) -> None:
    """The row/discriminator invariant: bot_type follows the applied seed.

    The API layer guards same-type re-clones in Phase 2, but the service
    keeps the invariant every other write path enforces (defence in depth:
    a hello→hello adoption must never leave a generic row behind).
    """
    bot = _hello_bot_row(get_template("hello_world").seed.model_dump())
    merged, _invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, get_template("hello_world")))

    assert merged[0].bot_type is BotType.HELLO  # type set from the seed's own flow


def test_reclone_bot_without_config_row_creates_one(monkeypatch) -> None:
    """A legacy row with no BotConfig at all: re-clone seeds it fresh."""
    bot = _hello_bot_row({"bot_type": "hello"})
    bot.config = None
    merged, invalidate = _install_reclone(monkeypatch)

    asyncio.run(svc.reclone_bot(bot, get_template("hello_world")))

    flow = merged[0].config.flow
    assert flow["template"] == {"id": "hello_world", "version": 1}
    invalidate.assert_awaited_once_with(TOKEN)

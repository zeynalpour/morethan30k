"""Full-stack integration tests — real Postgres + real Redis, no mocks.

Runs against the shared host services from ``docker-compose.yml`` (the infra
stack: postgres on :5432, redis on :6379) but NEVER touches their data: it
creates a dedicated ``tme_test`` database, runs the real Alembic migrations,
exercises provision -> dashboard API (signed initData) -> config cache, then
drops the database and flushes Redis db 15. Skipped when the services are
unreachable.

One event loop drives the whole module (pools are loop-bound): setup, the
provisioning service calls, the API requests (httpx ASGITransport, no
TestClient portal), and teardown. The app's session factory and Redis client
are monkeypatched to the test resources for the module's lifetime.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Iterator
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import TypeVar
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import asyncpg
import httpx
from pydantic import SecretStr
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tme import main
from tme.config import settings
from tme.core import cache as cache_module
from tme.core.i18n import localize
from tme.database import engine as engine_module
from tme.database.models import Bot as BotModel, BotConfig, BotType, User
from tme.services import user_language as user_language_module
from tme.services.managed_bots import provision_managed_bot
from tme.services.user_language import get_user_language, set_user_language
from tme.services.vault import (
    delete_bot_token,
    load_bot_token,
    resolve_bot_token,
    store_bot_token,
    token_hash,
)
from tme.templates import get_template

REPO = Path(__file__).resolve().parents[1]
ADMIN_URL = "postgresql://tme:tme@localhost:5432/tme"
TEST_DB_URL = "postgresql+asyncpg://tme:tme@localhost:5432/tme_test"
TEST_REDIS_URL = "redis://localhost:6379/15"
OWNER_TG_ID = 424242
FAKE_TOKEN = "987654321:AAH4sFakeFakeFakeFakeFakeFakeFakeFakeFakeFake"
#: A second token for tests that need a fresh row — the shared module-scoped
#: stack provisions idempotently ON TOKEN, so reusing FAKE_TOKEN would
#: inherit an earlier test's bot_type/config.
FRESH_TOKEN = "112233445:AAH4sFakeFakeFakeFakeFakeFakeFakeFakeFakeFake"

_T = TypeVar("_T")
Run = Callable[[Awaitable[_T]], _T]


def _host_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (_host_reachable("127.0.0.1", 5432) and _host_reachable("127.0.0.1", 6379)),
    reason="host postgres/redis (docker-compose.yml infra stack) not reachable",
)


def _sign_init_data(*, user_id: int, auth_date: int | None = None) -> str:
    """Sign initData exactly as Telegram does (HMAC with the main bot token)."""
    bot_token = settings.main_bot_token.get_secret_value()
    payload = {
        "auth_date": str(auth_date or int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Owner"}),
    }
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    payload["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


async def _create_test_db() -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute("DROP DATABASE IF EXISTS tme_test")
        await conn.execute("CREATE DATABASE tme_test")
    finally:
        await conn.close()


async def _drop_test_db() -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute("DROP DATABASE IF EXISTS tme_test")
    finally:
        await conn.close()


def _run_migrations() -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DB_URL}
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO,
        env=env,
        check=True,
        capture_output=True,
    )


async def _api(method: str, path: str, **kwargs) -> httpx.Response:
    """Call the real FastAPI app in-process (no network, no portal threads)."""
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.request(method, path, **kwargs)


@pytest.fixture(scope="module")
def test_stack() -> Iterator[Run]:
    """Point the app's DB/Redis at dedicated test resources; run all async on
    one loop so engine/redis pools never cross event loops."""
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_create_test_db())
    _run_migrations()

    test_engine = create_async_engine(TEST_DB_URL)
    old_factory = engine_module.SessionFactory
    engine_module.SessionFactory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )

    test_redis = aioredis.from_url(TEST_REDIS_URL, decode_responses=False)
    old_redis = cache_module.redis_client
    cache_module.redis_client = test_redis
    # user_language binds redis_client at import time — repoint it too.
    old_ul_redis = user_language_module.redis_client
    user_language_module.redis_client = test_redis

    def run(coro: Awaitable[_T]) -> _T:
        return loop.run_until_complete(coro)

    yield run

    engine_module.SessionFactory = old_factory
    cache_module.redis_client = old_redis
    user_language_module.redis_client = old_ul_redis
    loop.run_until_complete(test_redis.flushdb())
    loop.run_until_complete(test_redis.aclose())
    loop.run_until_complete(test_engine.dispose())
    loop.run_until_complete(_drop_test_db())
    loop.close()


def _provision(run: Run, monkeypatch) -> BotModel:
    """Provision a real bot row in the test DB (webhook skipped, getMe fails fast)."""
    monkeypatch.setattr("tme.services.managed_bots.register_webhook", AsyncMock(return_value=False))
    return run(
        provision_managed_bot(
            token=FAKE_TOKEN, owner_telegram_id=OWNER_TG_ID, bot_type=BotType.GENERIC
        )
    )


def _auth_headers() -> dict[str, str]:
    return {"X-Telegram-Init-Data": _sign_init_data(user_id=OWNER_TG_ID)}


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_provision_then_dashboard_lists_and_reads_config(test_stack, monkeypatch) -> None:
    bot_row = _provision(test_stack, monkeypatch)
    assert bot_row.is_active is True

    resp = test_stack(_api("GET", "/api/bots", headers=_auth_headers()))
    assert resp.status_code == 200
    assert any(b["id"] == bot_row.id for b in resp.json())

    cfg = test_stack(_api("GET", f"/api/bots/{bot_row.id}/config", headers=_auth_headers()))
    assert cfg.status_code == 200
    assert cfg.json()["bot_type"] == "generic"
    assert "welcome_message" in cfg.json()


def test_config_patch_goes_live_through_cache(test_stack, monkeypatch) -> None:
    bot_row = _provision(test_stack, monkeypatch)

    # The fixture bot's token is fake, so a real liveness probe marks it
    # archived — and the write-path guard refuses edits on archived bots
    # (a bot deleted in BotFather must not be editable). This test is about
    # the config cache path, so pin the cached verdict to "unknown".
    async def _unknown_verdict(_bot_id):
        return None

    monkeypatch.setattr("tme.api.routes.cached_bot_liveness", _unknown_verdict)

    resp = test_stack(
        _api(
            "PATCH",
            f"/api/bots/{bot_row.id}/config",
            json={
                "flow": {
                    "bot_type": "generic",
                    "welcome_message": "INTEGRATION-TEST",
                    "menu_buttons": [{"text": "X", "callback": "x"}],
                    "translations": {
                        "fa": {"welcome_message": "به‌روزرسانی فارسی", "fallback_message": "پاسخ"},
                        "en": {"welcome_message": "Updated (en)"},
                    },
                }
            },
            headers=_auth_headers(),
        )
    )
    assert resp.status_code == 200

    # The runtime path reads through Redis — must see the edit immediately.
    live = test_stack(cache_module.get_bot_config(FAKE_TOKEN))
    assert live is not None
    assert live.welcome_message == "INTEGRATION-TEST"

    # Phase 1: translations ride the same flow dict through the cache.
    assert localize(live, "fa").welcome_message == "به‌روزرسانی فارسی"
    assert localize(live, "de").welcome_message == "Updated (en)"  # en fallback
    assert localize(live, "fa").fallback_message == "پاسخ"


def test_disable_and_reenable_toggle(test_stack, monkeypatch) -> None:
    bot_row = _provision(test_stack, monkeypatch)

    # Warm the cache first so disabling has to actively drop it.
    assert test_stack(cache_module.get_bot_config(FAKE_TOKEN)) is not None

    off = test_stack(
        _api("PATCH", f"/api/bots/{bot_row.id}", json={"is_active": False}, headers=_auth_headers())
    )
    assert off.status_code == 200
    assert off.json()["is_active"] is False
    # Disabled bot is unresolvable at the runtime layer (hard stop).
    assert test_stack(cache_module.get_bot_config(FAKE_TOKEN)) is None

    on = test_stack(
        _api("PATCH", f"/api/bots/{bot_row.id}", json={"is_active": True}, headers=_auth_headers())
    )
    assert on.status_code == 200
    assert test_stack(cache_module.get_bot_config(FAKE_TOKEN)) is not None


def test_type_switch_resets_flow_to_defaults(test_stack, monkeypatch) -> None:
    bot_row = _provision(test_stack, monkeypatch)

    resp = test_stack(
        _api("PATCH", f"/api/bots/{bot_row.id}", json={"bot_type": "echo"}, headers=_auth_headers())
    )

    assert resp.status_code == 200
    assert resp.json()["bot_type"] == "echo"
    live = test_stack(cache_module.get_bot_config(FAKE_TOKEN))
    assert live is not None
    assert live.bot_type == "echo"  # flow reset to echo default, cache re-read


def test_user_language_preference_round_trip(test_stack) -> None:
    """S1.2 /language storage works against the real DB + Redis cache."""
    tg_id = 555001
    assert test_stack(get_user_language(tg_id)) is None
    test_stack(set_user_language(tg_id, "fa"))
    assert test_stack(get_user_language(tg_id)) == "fa"


def test_reclone_end_to_end_through_cache(test_stack, monkeypatch) -> None:
    """S2.3 end-to-end: provision from a template → owner customizes →
    re-clone → the runtime serves the fresh seed + kept owner layer.

    Exercises every layer for real: the registry seed, the signed-initData
    owner scope, the persist-then-invalidate cache path, and the Phase 1
    localize chain over the preserved translations.
    """
    # A hello bot cloned from hello_world v1 (the S2.2 picker path).
    monkeypatch.setattr("tme.services.managed_bots.register_webhook", AsyncMock(return_value=False))
    bot_row = test_stack(
        provision_managed_bot(
            token=FRESH_TOKEN,
            owner_telegram_id=OWNER_TG_ID,
            seed=get_template("hello_world").seed,
        )
    )
    assert bot_row.bot_type is BotType.HELLO

    # The owner edits the base copy + adds a translation (the dashboard path).
    edited = get_template("hello_world").seed.model_dump()
    edited["greeting"] = "OWNER EDIT"
    edited["translations"] = {"fa": {"greeting": "سلام ویرایش"}}
    edited["single_language"] = False  # translations visible while editing
    patch = test_stack(
        _api(
            "PATCH",
            f"/api/bots/{bot_row.id}/config",
            json={"flow": edited},
            headers=_auth_headers(),
        )
    )
    assert patch.status_code == 200
    live = test_stack(cache_module.get_bot_config(FRESH_TOKEN))
    assert live is not None
    assert live.greeting == "OWNER EDIT"
    assert localize(live, "fa").greeting == "سلام ویرایش"  # owner layer live

    # Now the owner locks single-language mode and re-clones: the mode must
    # survive the reset (single_language is the OWNER's, never the seed's).
    edited["single_language"] = True
    lock = test_stack(
        _api(
            "PATCH",
            f"/api/bots/{bot_row.id}/config",
            json={"flow": edited},
            headers=_auth_headers(),
        )
    )
    assert lock.status_code == 200

    # Provenance reads back through the API.
    prov = test_stack(_api("GET", f"/api/bots/{bot_row.id}/template", headers=_auth_headers()))
    assert prov.status_code == 200
    assert prov.json()["current"] == {"id": "hello_world", "version": 1}
    assert prov.json()["update_available"] is False  # registry not ahead of v1

    # The explicit owner action: reset to the template's latest seed.
    reclone = test_stack(
        _api(
            "POST",
            f"/api/bots/{bot_row.id}/reclone",
            json={"template_id": "hello_world"},
            headers=_auth_headers(),
        )
    )
    assert reclone.status_code == 200
    assert reclone.json()["bot_type"] == "hello"

    # The runtime reads the RESET flow through the invalidated cache: fresh
    # seed copy, owner translations + single_language preserved, new stamp.
    live = test_stack(cache_module.get_bot_config(FRESH_TOKEN))
    assert live is not None
    assert live.greeting == "Hello there! 👋"  # owner edit replaced
    assert live.single_language is True  # owner mode survived
    # The kept translation is IN the flow (single-language mode mutes the
    # localize chain by design — Phase 1 — so assert on the stored layer).
    assert live.translations["fa"].greeting == "سلام ویرایش"
    # Un-lock the mode: the preserved translation immediately localizes.
    flow = live.model_dump()
    flow["single_language"] = False
    unlock = test_stack(
        _api(
            "PATCH",
            f"/api/bots/{bot_row.id}/config",
            json={"flow": flow},
            headers=_auth_headers(),
        )
    )
    assert unlock.status_code == 200
    live2 = test_stack(cache_module.get_bot_config(FRESH_TOKEN))
    assert live2 is not None
    assert localize(live2, "fa").greeting == "سلام ویرایش"  # owner layer intact

    cfg = test_stack(_api("GET", f"/api/bots/{bot_row.id}/config", headers=_auth_headers()))
    assert cfg.json()["template"] == {"id": "hello_world", "version": 1}

    # Another owner's scope: the re-clone route is 404 for a stranger.
    stranger = test_stack(
        _api(
            "POST",
            f"/api/bots/{bot_row.id}/reclone",
            json={"template_id": "hello_world"},
            headers={"X-Telegram-Init-Data": _sign_init_data(user_id=999)},
        )
    )
    assert stranger.status_code == 404


# --------------------------------------------------------------------------- #
# S0.3 activation (issue #28) — hash routing, the token accessor, the backfill
#
# ORDER MATTERS, and it is the rollout order:
#   hash routing → fallback → invalidation → negative cache → token accessor
#   → backfill dry run → backfill --apply (idempotent) → abort-on-mismatch
#   → --clear-plaintext (LAST: it clears every row in tme_test, and the tests
#      after it prove resolution still works with no plaintext anywhere).
# --------------------------------------------------------------------------- #

#: One deterministic master key for the whole module: vault rows written by an
#: earlier test must still decrypt in a later one (a random per-test key would
#: make every earlier row undecryptable).
_MASTER_KEY = base64.b64encode(hashlib.sha256(b"tme-integration-vault-key").digest()).decode()

_HASHED_TOKEN = "910000001:HASH-ROUTED-TOKEN"
_FALLBACK_TOKEN = "910000002:UNMIGRATED-TOKEN"
_ACCESSOR_TOKEN = "910000004:ACCESSOR-TOKEN"
_BACKFILL_TOKEN = "910000006:BACKFILL-TOKEN"
_IDEMPOTENT_TOKEN = "910000008:IDEMPOTENT-TOKEN"
_POISONED_TOKEN = "910000007:POISONED-TOKEN"

_BACKFILL_SCRIPT = REPO / "scripts" / "vault_backfill.py"


def _load_backfill():
    """Import ``scripts/vault_backfill.py`` by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("vault_backfill", _BACKFILL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: a module-level @dataclass resolves its annotations
    # through sys.modules.
    sys.modules["vault_backfill"] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_backfill()


def _use_vault_key(monkeypatch) -> None:
    """Give this test a usable master key (the repo/CI sets none)."""
    monkeypatch.setattr(settings, "vault_master_key", SecretStr(_MASTER_KEY))


async def _owner_id(session, telegram_id: int = OWNER_TG_ID) -> int:
    """The owning user's row id, creating the user on first sight."""
    owner = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if owner is None:
        owner = User(telegram_id=telegram_id, username="owner")
        session.add(owner)
        await session.flush()
    return owner.id


async def _add_bot(
    *,
    token: str | None,
    token_hash_value: str | None,
    telegram_bot_id: int,
    flow: dict | None = None,
) -> int:
    """Insert a bot row directly (no provisioning) and return its id."""
    async with engine_module.session_scope() as session:
        bot = BotModel(
            token=token,
            token_hash=token_hash_value,
            telegram_bot_id=telegram_bot_id,
            owner_id=await _owner_id(session),
            is_active=True,
        )
        bot.config = BotConfig(
            flow=flow or {"bot_type": "generic", "welcome_message": "HASH-ROUTED"}
        )
        session.add(bot)
        await session.flush()
        return bot.id


async def _store_token(bot_id: int, token: str) -> None:
    async with engine_module.session_scope() as session:
        await store_bot_token(session, bot_id=bot_id, token=token)


async def _forget_token(bot_id: int) -> None:
    async with engine_module.session_scope() as session:
        await delete_bot_token(session, bot_id=bot_id)


async def _vaulted_value(bot_id: int) -> str | None:
    async with engine_module.session_scope() as session:
        return await load_bot_token(session, bot_id=bot_id)


async def _bot_state(bot_id: int) -> tuple[str | None, str | None]:
    """``(token, token_hash)`` straight from Postgres."""
    async with engine_module.session_scope() as session:
        bot = (
            await session.execute(select(BotModel).where(BotModel.id == bot_id))
        ).scalar_one_or_none()
        assert bot is not None
        return bot.token, bot.token_hash


async def _plaintext_count() -> int:
    async with engine_module.session_scope() as session:
        rows = await session.execute(select(BotModel.token))
        return sum(1 for (token,) in rows if token is not None)


async def _all_states() -> list[tuple[int, str | None, str | None]]:
    """Every bot's ``(id, token, token_hash)`` — the whole table, sorted."""
    async with engine_module.session_scope() as session:
        rows = await session.execute(select(BotModel.id, BotModel.token, BotModel.token_hash))
        return sorted((bot_id, token, hash_) for bot_id, token, hash_ in rows)


async def _resolve_token(bot_id: int) -> str | None:
    """Resolve one bot through the S0.3 accessor (vault first)."""
    async with engine_module.session_scope() as session:
        bot = (
            await session.execute(select(BotModel).where(BotModel.id == bot_id))
        ).scalar_one_or_none()
        assert bot is not None
        return await resolve_bot_token(session, bot)


def test_hash_routing_resolves_a_row_with_no_plaintext(test_stack, monkeypatch) -> None:
    """A webhook token captured purely by ``token_hash`` returns the right config.

    The row's plaintext column is already NULL (the post-backfill state), so a
    hash lookup is the only way it can resolve — and the cache entry must be
    keyed by the hash, never by the raw token.
    """
    _use_vault_key(monkeypatch)
    raw = _HASHED_TOKEN
    bot_id = test_stack(
        _add_bot(token=None, token_hash_value=token_hash(raw), telegram_bot_id=910000001)
    )
    test_stack(_store_token(bot_id, raw))  # vaulted, plaintext already cleared

    live = test_stack(cache_module.get_bot_config(raw))
    assert live is not None
    assert live.welcome_message == "HASH-ROUTED"

    redis = cache_module.redis_client
    assert test_stack(redis.get(f"botcfg:{token_hash(raw)}")) is not None
    assert test_stack(redis.get(f"botcfg:{raw}")) is None  # never keyed by plaintext


def test_plaintext_fallback_still_resolves_an_unmigrated_row(test_stack) -> None:
    """Rows the backfill has not touched keep working (transitional fallback)."""
    raw = _FALLBACK_TOKEN
    test_stack(_add_bot(token=raw, token_hash_value=None, telegram_bot_id=910000002))

    live = test_stack(cache_module.get_bot_config(raw))
    assert live is not None
    assert live.welcome_message == "HASH-ROUTED"
    # …and it is cached under the hash the fallback implicitly computed.
    assert test_stack(cache_module.redis_client.get(f"botcfg:{token_hash(raw)}")) is not None


def test_invalidation_drops_the_hash_keyed_entry(test_stack) -> None:
    """Config invalidation must clear ``botcfg:{token_hash}``, not the old key."""
    raw = _FALLBACK_TOKEN
    key = f"botcfg:{token_hash(raw)}"

    assert test_stack(cache_module.get_bot_config(raw)) is not None
    assert test_stack(cache_module.redis_client.get(key)) is not None

    test_stack(cache_module.invalidate_bot_config(raw))

    assert test_stack(cache_module.redis_client.get(key)) is None
    assert test_stack(cache_module.redis_client.get(f"botcfg:{raw}")) is None


def test_unknown_token_is_negatively_cached_for_one_minute(test_stack) -> None:
    """The negative cache (and its TTL) survives the switch to hash keys."""
    unknown = "999999999:NEVER-PROVISIONED"
    key = f"botcfg:{token_hash(unknown)}"

    assert test_stack(cache_module.get_bot_config(unknown)) is None
    assert test_stack(cache_module.redis_client.get(key)) == b"\x00"
    ttl = test_stack(cache_module.redis_client.ttl(key))
    assert 0 < ttl <= 60
    assert test_stack(cache_module.redis_client.get(f"botcfg:{unknown}")) is None


def test_token_accessor_prefers_the_vault_then_falls_back(test_stack, monkeypatch) -> None:
    """The one accessor: vault wins, the transitional column answers otherwise."""
    _use_vault_key(monkeypatch)
    raw = _ACCESSOR_TOKEN
    bot_id = test_stack(
        _add_bot(token=raw, token_hash_value=token_hash(raw), telegram_bot_id=910000004)
    )

    # Not vaulted yet → the plaintext column answers (rollout state).
    assert test_stack(_resolve_token(bot_id)) == raw

    # A rotated token lives in the vault: the vault wins over the stale column.
    test_stack(_store_token(bot_id, raw + "-ROTATED"))
    assert test_stack(_resolve_token(bot_id)) == raw + "-ROTATED"

    # Vault row gone → back to the plaintext fallback (no crash, no stale value).
    test_stack(_forget_token(bot_id))
    assert test_stack(_resolve_token(bot_id)) == raw


def test_without_a_master_key_behaviour_is_unchanged(test_stack, monkeypatch, caplog) -> None:
    """No ``VAULT_MASTER_KEY`` → today's behaviour byte-for-byte, plus the warning."""
    monkeypatch.setattr(settings, "vault_master_key", None)
    monkeypatch.setattr("tme.services.managed_bots.register_webhook", AsyncMock(return_value=False))
    raw = "910000005:NO-KEY-TOKEN"

    bot_row = test_stack(provision_managed_bot(token=raw, owner_telegram_id=OWNER_TG_ID))

    assert bot_row.token == raw  # plaintext stays the source of truth
    assert bot_row.token_hash == token_hash(raw)  # hash is set either way
    assert test_stack(_vaulted_value(bot_row.id)) is None  # nothing vaulted
    assert "VAULT_MASTER_KEY not set" in caplog.text  # the loud warning
    assert test_stack(cache_module.get_bot_config(raw)) is not None  # still serving


def test_backfill_dry_run_writes_nothing(test_stack, monkeypatch) -> None:
    """Dry run is the default and must be a pure read (plus its own report)."""
    _use_vault_key(monkeypatch)
    raw = _BACKFILL_TOKEN
    bot_id = test_stack(_add_bot(token=raw, token_hash_value=None, telegram_bot_id=910000006))
    before = test_stack(_all_states())

    summary = test_stack(backfill.run_backfill(apply=False))

    assert summary.hash_set >= 1  # this row would get its hash
    assert summary.vaulted >= 1  # …and its token vaulted
    assert summary.cleared == 0
    assert summary.failed == 0
    assert test_stack(_all_states()) == before  # nothing written
    assert test_stack(_vaulted_value(bot_id)) is None


def test_backfill_dry_run_detects_a_hash_mismatch_without_repairing_it(
    test_stack, monkeypatch
) -> None:
    """A rotated pepper is reported (hash-set) but never silently rewritten."""
    _use_vault_key(monkeypatch)
    monkeypatch.setattr(settings, "vault_pepper", SecretStr("rotated-pepper"))
    before = test_stack(_all_states())

    summary = test_stack(backfill.run_backfill(apply=False))

    assert summary.hash_set >= 1  # every stored hash no longer matches
    assert summary.cleared == 0
    assert test_stack(_all_states()) == before


def test_backfill_apply_is_idempotent(test_stack, monkeypatch) -> None:
    """``--apply`` hashes + vaults; a second run changes nothing at all."""
    _use_vault_key(monkeypatch)
    raw = _IDEMPOTENT_TOKEN
    bot_id = test_stack(_add_bot(token=raw, token_hash_value=None, telegram_bot_id=910000008))

    first = test_stack(backfill.run_backfill(apply=True))
    assert first.hash_set >= 1
    assert first.vaulted >= 1
    assert first.failed == 0
    assert first.cleared == 0  # never without --clear-plaintext

    assert test_stack(_bot_state(bot_id)) == (raw, token_hash(raw))
    assert test_stack(_vaulted_value(bot_id)) == raw

    after_first = test_stack(_all_states())
    second = test_stack(backfill.run_backfill(apply=True))

    assert (second.hash_set, second.vaulted, second.cleared, second.failed) == (0, 0, 0, 0)
    assert second.verified >= 1
    assert test_stack(_all_states()) == after_first


def test_backfill_aborts_before_clearing_when_a_round_trip_fails(test_stack, monkeypatch) -> None:
    """A vault/plaintext mismatch stops the run and clears nothing."""
    _use_vault_key(monkeypatch)
    raw = _POISONED_TOKEN
    bot_id = test_stack(_add_bot(token=raw, token_hash_value=None, telegram_bot_id=910000007))
    # A vault row holding a different value: one of the two copies is stale.
    test_stack(_store_token(bot_id, "910000007:SOMETHING-ELSE"))
    before = test_stack(_all_states())

    with pytest.raises(backfill.VaultBackfillError):
        test_stack(backfill.run_backfill(apply=True, clear_plaintext=True))

    # Nothing was cleared (and the aborted batch rolled back) anywhere.
    assert test_stack(_all_states()) == before
    assert test_stack(_plaintext_count()) == len([s for s in before if s[1] is not None])

    # The mismatch was the only problem: dropping it lets the run complete.
    test_stack(_forget_token(bot_id))
    recovered = test_stack(backfill.run_backfill(apply=True))
    assert recovered.failed == 0
    assert test_stack(_vaulted_value(bot_id)) == raw


def test_cli_refuses_clear_plaintext_without_apply(capsys) -> None:
    """``--clear-plaintext`` alone is a usage error, not a silent no-op."""
    assert backfill.main(["--clear-plaintext"]) == 2
    assert "--apply" in capsys.readouterr().err


def test_backfill_clear_plaintext_rollout_then_hash_routing_only(test_stack, monkeypatch) -> None:
    """The payoff: with every plaintext token gone, bots still resolve + send.

    Runs last on purpose — it clears ``bots.token`` for the whole test database.
    """
    _use_vault_key(monkeypatch)
    raw = _HASHED_TOKEN  # already has plaintext NULL + a vault row
    fallback_raw = _FALLBACK_TOKEN

    summary = test_stack(backfill.run_backfill(apply=True, clear_plaintext=True))

    assert summary.failed == 0
    assert summary.cleared >= 1
    assert test_stack(_plaintext_count()) == 0  # no plaintext token left anywhere

    ids = test_stack(_all_states())
    assert all(token is None for _bot_id, token, _hash in ids)
    assert all(hash_ is not None for _bot_id, _token, hash_ in ids)

    # Resolution is now purely hash-based, and the accessor still hands the
    # real token to the send path (from the vault).
    assert test_stack(cache_module.get_bot_config(fallback_raw)) is not None
    assert test_stack(cache_module.get_bot_config(raw)) is not None
    cleared_id = next(bot_id for bot_id, token, _hash in ids if token is None)
    resolved = test_stack(_resolve_token(cleared_id))
    assert resolved is not None
    assert resolved == test_stack(_vaulted_value(cleared_id))


def test_backfill_repairs_hashes_from_the_vault_after_clearing(test_stack, monkeypatch) -> None:
    """A pepper rotation after ``--clear-plaintext`` is repaired from the vault.

    With the plaintext column already NULL the vaulted token is the only
    source of truth, so the hash has to be re-derived by decrypting it — which
    is exactly what a post-rollout ``VAULT_PEPPER`` rotation needs. Runs last:
    it re-derives every hash in the test database under a rotated pepper.
    """
    _use_vault_key(monkeypatch)
    raw = "910000009:ROTATED-PEPPER-TOKEN"
    stale_hash = token_hash(raw)  # the pre-rotation hash
    bot_id = test_stack(
        _add_bot(token=None, token_hash_value=stale_hash, telegram_bot_id=910000009)
    )
    test_stack(_store_token(bot_id, raw))  # vaulted; plaintext already NULL

    monkeypatch.setattr(settings, "vault_pepper", SecretStr("rotated-pepper-after-clear"))
    assert test_stack(_bot_state(bot_id)) == (None, stale_hash)  # stale + no fallback

    summary = test_stack(backfill.run_backfill(apply=True))

    assert summary.failed == 0
    assert summary.hash_set >= 1  # re-derived from the vault, not the column
    assert summary.cleared == 0  # still only ever with the flag
    assert test_stack(_bot_state(bot_id)) == (None, token_hash(raw))

    live = test_stack(cache_module.get_bot_config(raw))
    assert live is not None
    assert live.welcome_message == "HASH-ROUTED"


# --------------------------------------------------------------------------- #
# S0.3 follow-up — provisioning after activation is a VAULT-ONLY write
# --------------------------------------------------------------------------- #


def test_provisioning_with_the_key_writes_the_vault_only(test_stack, monkeypatch) -> None:
    """A bot created while the master key is set has NO plaintext token.

    Runs after the rollout tests on purpose: by then every other row's
    ``bots.token`` is already NULL, and this proves the NEXT bot the owner
    creates lands in the same state instead of re-introducing a plaintext copy
    the backfill would have to clear again. The vault row and the routing hash
    are the only token artefacts, so hash-first resolution and the accessor keep
    working end to end.
    """
    _use_vault_key(monkeypatch)
    monkeypatch.setattr("tme.services.managed_bots.register_webhook", AsyncMock(return_value=False))
    raw = "910000010:VAULT-ONLY-TOKEN"

    bot_row = test_stack(provision_managed_bot(token=raw, owner_telegram_id=OWNER_TG_ID))

    plaintext, hash_ = test_stack(_bot_state(bot_row.id))
    assert plaintext is None  # vault-only: the column was never written
    assert hash_ == token_hash(raw)  # the routing key is always set

    assert test_stack(_vaulted_value(bot_row.id)) == raw
    assert test_stack(_resolve_token(bot_row.id)) == raw  # the accessor finds it
    assert test_stack(cache_module.get_bot_config(raw)) is not None  # and it serves
    assert test_stack(_plaintext_count()) == 0  # nothing unencrypted anywhere

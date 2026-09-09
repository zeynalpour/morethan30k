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
from collections.abc import Awaitable, Callable, Iterator
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import TypeVar
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tme import main
from tme.config import settings
from tme.core import cache as cache_module
from tme.core.i18n import localize
from tme.database import engine as engine_module
from tme.database.models import Bot as BotModel, BotType
from tme.services import user_language as user_language_module
from tme.services.managed_bots import provision_managed_bot
from tme.services.user_language import get_user_language, set_user_language

REPO = Path(__file__).resolve().parents[1]
ADMIN_URL = "postgresql://tme:tme@localhost:5432/tme"
TEST_DB_URL = "postgresql+asyncpg://tme:tme@localhost:5432/tme_test"
TEST_REDIS_URL = "redis://localhost:6379/15"
OWNER_TG_ID = 424242
FAKE_TOKEN = "987654321:AAH4sFakeFakeFakeFakeFakeFakeFakeFakeFake"

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

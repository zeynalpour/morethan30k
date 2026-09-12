"""Tests for tenant-bot liveness detection (deleted-in-BotFather sync).

Telegram sends no "bot deleted" event, so TME probes: a revoked token is
rejected by ``getMe``. These tests pin the two things that matter —
an explicit rejection means *dead*, and anything inconclusive fails *open*
(a network blip must never archive a healthy bot) — plus the Redis cache.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.methods import GetMe
import pytest

from tme.services import bot_health


class _StubBot:
    """Stands in for ``aiogram.Bot`` so probe_token never touches the network."""

    behaviour: str = "ok"

    def __init__(self, token: str) -> None:
        self.token = token

    async def __aenter__(self) -> _StubBot:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_me(self) -> SimpleNamespace:
        if self.behaviour == "revoked":
            raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")
        if self.behaviour == "boom":
            raise TelegramBadRequest(method=GetMe(), message="Bad Gateway")
        return SimpleNamespace(id=1, username="stub")


class _FakeRedis:
    """Minimal async Redis stand-in (get/set/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expiries: dict[str, int | None] = {}
        self.sets = 0

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.sets += 1
        self.store[key] = value
        self.expiries[key] = ex

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(bot_health, "redis_client", fake)
    return fake


def _use(monkeypatch, behaviour: str) -> None:
    monkeypatch.setattr(bot_health, "AioBot", _StubBot)
    _StubBot.behaviour = behaviour


class TestProbeToken:
    def test_live_token_is_alive(self, monkeypatch) -> None:
        _use(monkeypatch, "ok")
        assert _run(bot_health.probe_token("123:live")) is True

    def test_revoked_token_is_dead(self, monkeypatch) -> None:
        """Deleting the bot in BotFather revokes its token → 401."""
        _use(monkeypatch, "revoked")
        assert _run(bot_health.probe_token("123:dead")) is False

    def test_transient_api_error_fails_open(self, monkeypatch) -> None:
        """A Telegram hiccup must never archive a healthy bot."""
        _use(monkeypatch, "boom")
        assert _run(bot_health.probe_token("123:flaky")) is True

    def test_unexpected_error_fails_open(self, monkeypatch) -> None:
        class _Exploding(_StubBot):
            async def get_me(self):
                raise RuntimeError("socket closed")

        monkeypatch.setattr(bot_health, "AioBot", _Exploding)
        assert _run(bot_health.probe_token("123:flaky")) is True


class TestCaching:
    def test_verdict_is_cached_with_ttl(self, monkeypatch, fake_redis) -> None:
        _use(monkeypatch, "revoked")

        assert _run(bot_health.bot_is_alive(7, "123:dead")) is False
        assert fake_redis.store["botlive:7"] == b"\x00"
        assert fake_redis.expiries["botlive:7"] == bot_health.LIVENESS_TTL

    def test_cache_hit_skips_the_probe(self, monkeypatch, fake_redis) -> None:
        _use(monkeypatch, "ok")
        fake_redis.store["botlive:7"] = b"\x00"  # cached as dead

        assert _run(bot_health.bot_is_alive(7, "123:live")) is False
        assert fake_redis.sets == 0  # no extra set, no probe

    def test_forget_drops_the_verdict(self, monkeypatch, fake_redis) -> None:
        fake_redis.store["botlive:7"] = b"\x01"
        _run(bot_health.forget_bot_liveness(7))
        assert "botlive:7" not in fake_redis.store


class TestProbeBots:
    def test_probes_many_and_returns_map(self, monkeypatch, fake_redis) -> None:
        _use(monkeypatch, "ok")

        result = _run(bot_health.probe_bots([(1, "a:1"), (2, "b:2")]))

        assert result == {1: True, 2: True}

    def test_empty_input_is_a_noop(self, monkeypatch, fake_redis) -> None:
        assert _run(bot_health.probe_bots([])) == {}


def _run(coro):
    """Run a coroutine without pytest-asyncio (repo's existing convention)."""
    return asyncio.run(coro)

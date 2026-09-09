"""Tests for the owner-scoped settings API (S0.4).

Uses ``TestClient`` WITHOUT the ``with`` context manager (lifespan never runs,
so no webhook registration over the network) and monkeypatched service/session
layers — same pattern as ``test_webhook.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tme import main
from tme.database.models import BotType

client = TestClient(main.app)

_OWNER_TELEGRAM_ID = 42
_AUTH = {"X-Telegram-Init-Data": "any-init-data"}


class _FakeScalars:
    def __init__(self, items) -> None:
        self._items = items

    def __iter__(self):
        return iter(self._items)


class _FakeResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _FakeScalars(self._value)


def _install(
    monkeypatch,
    *,
    bot_row,
    auth_id: int | None = _OWNER_TELEGRAM_ID,
    invalidate: AsyncMock | None = None,
):
    """Fake the initData auth, sessions, and cache invalidation."""
    monkeypatch.setattr("tme.api.routes.validate_telegram_init_data", lambda _h: auth_id)

    @asynccontextmanager
    async def scope():
        yield _FakeSession(bot_row)

    monkeypatch.setattr("tme.api.routes.session_scope", scope)

    if invalidate is None:
        invalidate = AsyncMock()
    monkeypatch.setattr("tme.api.routes.invalidate_bot_config", invalidate)
    return invalidate


class _FakeSession:
    def __init__(self, bot_row) -> None:
        self._bot_row = bot_row

    async def execute(self, _statement):
        return _FakeResult(self._bot_row)


def _bot(*, config_flow: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        username="mybot",
        title="My Bot",
        bot_type=BotType.GENERIC,
        is_active=True,
        webhook_registered=True,
        created_at=datetime.now(UTC),
        token="123456789:SECRET_TOKEN",
        config=SimpleNamespace(flow=config_flow or {"bot_type": "generic"}),
    )


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def test_dashboard_served_at_root() -> None:
    """The BotFather-registered domain root hosts the dashboard (profile Mini App)."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_missing_auth_401() -> None:
    assert client.get("/api/bots").status_code == 401


def test_invalid_token_401(monkeypatch) -> None:
    monkeypatch.setattr("tme.api.routes.validate_telegram_init_data", lambda _h: None)
    assert client.get("/api/bots", headers=_AUTH).status_code == 401


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #
def test_list_bots_returns_owner_bots_without_token(monkeypatch) -> None:
    _install(monkeypatch, bot_row=[_bot(), _bot(config_flow={"bot_type": "echo"})])

    resp = client.get("/api/bots", headers=_AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload) == 2
    assert "token" not in payload[0]  # secrets never serialize
    assert payload[0]["username"] == "mybot"


def test_get_bot_owned(monkeypatch) -> None:
    _install(monkeypatch, bot_row=_bot())
    resp = client.get("/api/bots/7", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["id"] == 7


def test_get_bot_not_owned_404(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)
    resp = client.get("/api/bots/999", headers=_AUTH)
    assert resp.status_code == 404


def test_get_config_returns_flow(monkeypatch) -> None:
    _install(
        monkeypatch, bot_row=_bot(config_flow={"bot_type": "generic", "welcome_message": "hi"})
    )
    resp = client.get("/api/bots/7/config", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["welcome_message"] == "hi"


# --------------------------------------------------------------------------- #
# Config writes
# --------------------------------------------------------------------------- #
def test_patch_config_updates_flow_type_and_invalidates_cache(monkeypatch) -> None:
    bot = _bot()
    invalidate = _install(monkeypatch, bot_row=bot)

    resp = client.patch(
        "/api/bots/7/config",
        json={"flow": {"bot_type": "echo", "echo_prefix": ">>", "welcome_message": "Echo!"}},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json()["bot_type"] == "echo"
    assert bot.config.flow["echo_prefix"] == ">>"
    assert bot.bot_type is BotType.ECHO  # type column synced with the flow
    invalidate.assert_awaited_once_with("123456789:SECRET_TOKEN")


def test_patch_config_rejects_unknown_type(monkeypatch) -> None:
    bot = _bot()
    invalidate = _install(monkeypatch, bot_row=bot)

    resp = client.patch("/api/bots/7/config", json={"flow": {"bot_type": "alien"}}, headers=_AUTH)

    assert resp.status_code == 422
    invalidate.assert_not_awaited()


def test_patch_config_not_owned_404(monkeypatch) -> None:
    invalidate = _install(monkeypatch, bot_row=None)
    resp = client.patch(
        "/api/bots/999/config",
        json={"flow": {"bot_type": "generic"}},
        headers=_AUTH,
    )
    assert resp.status_code == 404
    invalidate.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Management knobs (PATCH /api/bots/{id})
# --------------------------------------------------------------------------- #
def test_toggle_active_disables_bot(monkeypatch) -> None:
    bot = _bot()
    invalidate = _install(monkeypatch, bot_row=bot)

    resp = client.patch("/api/bots/7", json={"is_active": False}, headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert bot.is_active is False
    invalidate.assert_awaited_once()


def test_toggle_active_reenables_bot(monkeypatch) -> None:
    bot = _bot()
    _install(monkeypatch, bot_row=bot)

    resp = client.patch("/api/bots/7", json={"is_active": True}, headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json()["is_active"] is True
    assert bot.is_active is True


def test_switch_type_resets_flow_to_default(monkeypatch) -> None:
    bot = _bot()
    _install(monkeypatch, bot_row=bot)

    resp = client.patch("/api/bots/7", json={"bot_type": "echo"}, headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json()["bot_type"] == "echo"
    assert bot.bot_type is BotType.ECHO
    assert bot.config.flow["bot_type"] == "echo"  # flow reset to echo default


def test_update_bot_rejects_unknown_type(monkeypatch) -> None:
    _install(monkeypatch, bot_row=_bot())

    resp = client.patch("/api/bots/7", json={"bot_type": "warp"}, headers=_AUTH)

    assert resp.status_code == 422


def test_update_bot_empty_payload_422(monkeypatch) -> None:
    _install(monkeypatch, bot_row=_bot())

    resp = client.patch("/api/bots/7", json={}, headers=_AUTH)

    assert resp.status_code == 422


def test_update_bot_not_owned_404(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)

    resp = client.patch("/api/bots/999", json={"is_active": False}, headers=_AUTH)

    assert resp.status_code == 404

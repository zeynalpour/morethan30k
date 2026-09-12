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
from pydantic import TypeAdapter

from tme import main
from tme.database.models import BotType
from tme.schemas.bot_config import BotConfigUnion
from tme.templates import (
    STAMP_KEY,
    TemplateSpec,
    get_template,
    register,
)
from tme.templates.registry import REGISTRY

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


def test_patch_config_preserves_template_stamp(monkeypatch) -> None:
    """The stamp rides dashboard saves (ConfigEditor's ``...flow`` spread).

    Pinned per the Architect's S2.3 risk note: the stamp passes because
    ``BotConfigBase`` is ``extra="allow"`` — a future strict schema would
    silently strip it and this test would catch the regression.
    """
    bot = _bot()
    invalidate = _install(monkeypatch, bot_row=bot)

    resp = client.patch(
        "/api/bots/7/config",
        json={"flow": _stamped_hello_flow()},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json()["template"] == {"id": "hello_world", "version": 1}
    assert bot.config.flow["template"] == {"id": "hello_world", "version": 1}
    invalidate.assert_awaited_once_with("123456789:SECRET_TOKEN")


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


# --------------------------------------------------------------------------- #
# Templates (S2.3 — versioned templates + re-clone)
# --------------------------------------------------------------------------- #
def _stamped_hello_flow() -> dict:
    """A hello bot's flow as the S2.2 picker persists it (stamp included)."""
    return get_template("hello_world").seed.model_dump()


def _hello_bot(monkeypatch, flow: dict | None) -> tuple[SimpleNamespace, AsyncMock]:
    """Install an owned HELLO bot row with ``flow``; return (row, invalidate)."""
    row = _bot(config_flow=flow if flow is not None else {"bot_type": "hello"})
    row.bot_type = BotType.HELLO
    invalidate = _install(monkeypatch, bot_row=row)
    reclone = AsyncMock(return_value=row)
    monkeypatch.setattr("tme.api.routes.reclone_bot", reclone)
    return row, invalidate


class _MergeSession:
    """Fake session for the re-clone service: ``merge`` + ``flush`` only."""

    async def merge(self, obj):
        return obj

    async def flush(self) -> None:
        return None


def _bind_reclone_service(monkeypatch, invalidate: AsyncMock) -> None:
    """Run the REAL ``reclone_bot`` against the fakes the route tests already use.

    The service owns its transaction + cache invalidation (the S2.3 contract —
    the same shape as ``provision_managed_bot``), so a route test that wants
    the real persistence path binds the service's seams rather than mocking
    the function away. The SAME AsyncMock instance backs both layers, so
    ``invalidate.assert_*`` reads the call wherever it was made.
    """

    @asynccontextmanager
    async def scope():
        yield _MergeSession()

    monkeypatch.setattr("tme.services.managed_bots.session_scope", scope)
    monkeypatch.setattr("tme.services.managed_bots.invalidate_bot_config", invalidate)


def test_list_templates_owner_scoped(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)

    resp = client.get("/api/templates", headers=_AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    ids = [t["id"] for t in payload]
    assert ids == ["hello_world", "echo", "feedback_collector", "quiz", "simple_form"]
    hello = payload[0]
    assert hello == {
        "id": "hello_world",
        "version": 1,
        "bot_type": "hello",
        "display_name": "Hello World",
        "description": "A friendly greeter that says hi on /start and to every message.",
    }
    # No seed data leaks — the card is picker metadata only.
    assert "seed" not in hello


def test_list_templates_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr("tme.api.routes.validate_telegram_init_data", lambda _h: None)
    assert client.get("/api/templates", headers=_AUTH).status_code == 401
    assert client.get("/api/templates").status_code == 401


def test_get_bot_template_reports_lineage(monkeypatch) -> None:
    _hello_bot(monkeypatch, _stamped_hello_flow())

    resp = client.get("/api/bots/7/template", headers=_AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["current"] == {"id": "hello_world", "version": 1}
    assert payload["latest_version"] == 1
    assert payload["update_available"] is False


def test_get_bot_template_scratch_bot_has_no_lineage(monkeypatch) -> None:
    """Pre-Phase-2 / scratch flow: current null — adoption-ready, not an error."""
    _hello_bot(monkeypatch, {"bot_type": "hello", "greeting": "old starter"})

    resp = client.get("/api/bots/7/template", headers=_AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["current"] is None
    assert payload["latest_version"] is None
    assert payload["update_available"] is False


def test_get_bot_template_update_available_after_bump(monkeypatch) -> None:
    def _bump() -> TemplateSpec:
        """A hello_world v2 spec (mirrors the registry-bump data edit)."""
        dumped = _stamped_hello_flow()
        dumped["greeting"] = "v2 copy"
        dumped[STAMP_KEY] = {"id": "hello_world", "version": 2}
        return TemplateSpec(
            id="hello_world",
            version=2,
            bot_type=BotType.HELLO,
            display_name="Hello World",
            description="bump",
            seed=TypeAdapter(BotConfigUnion).validate_python(dumped),
        )

    _hello_bot(monkeypatch, _stamped_hello_flow())
    saved = {tid: list(specs) for tid, specs in REGISTRY.items()}
    try:
        register(_bump())
        resp = client.get("/api/bots/7/template", headers=_AUTH)
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["current"] == {"id": "hello_world", "version": 1}
    assert payload["latest_version"] == 2  # badge data, no mutation
    assert payload["update_available"] is True


def test_get_bot_template_not_owned_404(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)
    assert client.get("/api/bots/999/template", headers=_AUTH).status_code == 404


def test_reclone_happy_path_calls_service(monkeypatch) -> None:
    _row, _invalidate = _hello_bot(monkeypatch, _stamped_hello_flow())
    # Track what the service was asked to do.
    calls: list = []

    async def fake_reclone(bot, spec, preserve=None):
        calls.append((spec.id, spec.version, preserve))
        return bot

    monkeypatch.setattr("tme.api.routes.reclone_bot", fake_reclone)

    resp = client.post("/api/bots/7/reclone", json={"template_id": "hello_world"}, headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json()["id"] == 7
    assert resp.json()["bot_type"] == "hello"
    assert calls == [("hello_world", 1, None)]  # latest version, default preserve


def test_reclone_pins_explicit_version(monkeypatch) -> None:
    _hello_bot(monkeypatch, _stamped_hello_flow())
    calls: list = []

    async def fake_reclone(bot, spec, preserve=None):
        calls.append((spec.id, spec.version))
        return bot

    monkeypatch.setattr("tme.api.routes.reclone_bot", fake_reclone)

    resp = client.post(
        "/api/bots/7/reclone",
        json={"template_id": "hello_world", "version": 1},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    assert calls == [("hello_world", 1)]


def test_reclone_passes_preserve_whitelist(monkeypatch) -> None:
    _hello_bot(monkeypatch, _stamped_hello_flow())
    calls: list = []

    async def fake_reclone(bot, spec, preserve=None):
        calls.append(preserve)
        return bot

    monkeypatch.setattr("tme.api.routes.reclone_bot", fake_reclone)

    resp = client.post(
        "/api/bots/7/reclone",
        json={"template_id": "hello_world", "preserve": ["translations"]},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    assert calls == [["translations"]]


def test_reclone_unknown_template_404(monkeypatch) -> None:
    _hello_bot(monkeypatch, _stamped_hello_flow())

    resp = client.post("/api/bots/7/reclone", json={"template_id": "does_not_exist"}, headers=_AUTH)

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown template"


def test_reclone_cross_type_422(monkeypatch) -> None:
    """Phase 2 refuses cross-type re-clones — the type switch keeps its own path."""
    _row, invalidate = _hello_bot(monkeypatch, _stamped_hello_flow())
    reclone = AsyncMock()
    monkeypatch.setattr("tme.api.routes.reclone_bot", reclone)

    resp = client.post("/api/bots/7/reclone", json={"template_id": "quiz"}, headers=_AUTH)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "template bot_type mismatch"
    assert detail["template_bot_type"] == "generic"
    assert detail["bot_bot_type"] == "hello"
    reclone.assert_not_awaited()  # refused before any write
    invalidate.assert_not_awaited()


def test_reclone_same_type_adoption_on_unstamped_bot(monkeypatch) -> None:
    """A legacy/scratch hello bot adopts hello_world through the same path."""
    row, _invalidate = _hello_bot(monkeypatch, {"bot_type": "hello", "greeting": "old"})
    reclone = AsyncMock(return_value=row)
    monkeypatch.setattr("tme.api.routes.reclone_bot", reclone)

    resp = client.post("/api/bots/7/reclone", json={"template_id": "hello_world"}, headers=_AUTH)

    assert resp.status_code == 200
    assert reclone.await_args.args[1].id == "hello_world"


def test_reclone_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr("tme.api.routes.validate_telegram_init_data", lambda _h: None)
    resp = client.post("/api/bots/7/reclone", json={"template_id": "hello_world"}, headers=_AUTH)
    assert resp.status_code == 401
    assert (
        client.post("/api/bots/7/reclone", json={"template_id": "hello_world"}).status_code == 401
    )


def test_switch_type_clears_stale_stamp(monkeypatch) -> None:
    """A hand type-switch reset must not keep advertising the old lineage."""
    bot = _bot(config_flow=_stamped_hello_flow())
    bot.bot_type = BotType.HELLO
    _install(monkeypatch, bot_row=bot)

    resp = client.patch("/api/bots/7", json={"bot_type": "generic"}, headers=_AUTH)

    assert resp.status_code == 200
    assert bot.bot_type is BotType.GENERIC
    assert "template" not in bot.config.flow  # lineage cleared with the flow


def test_list_templates_returns_registry_latest(monkeypatch) -> None:
    _install(monkeypatch, bot_row=None)

    resp = client.get("/api/templates", headers=_AUTH)

    assert resp.status_code == 200
    payload = resp.json()
    ids = [t["id"] for t in payload]
    assert ids == ["hello_world", "echo", "feedback_collector", "quiz", "simple_form"]
    assert all(t["version"] == 1 for t in payload)
    # Serializes the registry's own metadata — no seed data leaks.
    assert payload[0]["display_name"] == "Hello World"
    assert "seed" not in payload[0]
    assert "greeting" not in payload[0]


def test_get_bot_template_stamped_flow(monkeypatch) -> None:
    stamped = get_template("quiz").seed.model_dump()
    _install(monkeypatch, bot_row=_bot(config_flow=stamped))

    resp = client.get("/api/bots/7/template", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == {"id": "quiz", "version": 1}
    assert body["latest_version"] == 1
    assert body["update_available"] is False


def test_get_bot_template_unstamped_flow(monkeypatch) -> None:
    # Scratch / pre-Phase-2 bot: no rider → adoption is the only lineage.
    _install(monkeypatch, bot_row=_bot(config_flow={"bot_type": "generic"}))

    resp = client.get("/api/bots/7/template", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] is None
    assert body["latest_version"] is None
    assert body["update_available"] is False


def test_reclone_happy_path_preserves_and_invalidates(monkeypatch) -> None:
    """Route → real service → persisted flow: base replaced, owner layer kept.

    The route is a thin adapter (auth + same-type guard); the service owns the
    write and the cache invalidation, so its seams are bound to the same fakes
    the route uses and the REAL service runs end to end.
    """
    old_flow = get_template("hello_world").seed.model_dump()
    old_flow["greeting"] = "OWNER EDIT"
    old_flow["translations"] = {"fa": {"greeting": "سلام"}}
    old_flow["single_language"] = True
    bot = _bot(config_flow=old_flow)
    bot.bot_type = BotType.HELLO  # a hello clone: the row agrees with its flow
    invalidate = _install(monkeypatch, bot_row=bot)
    _bind_reclone_service(monkeypatch, invalidate)

    resp = client.post(
        "/api/bots/7/reclone",
        json={"template_id": "hello_world"},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    assert resp.json()["bot_type"] == "hello"
    # Base replaced wholesale (owner's base edit gone)…
    assert bot.config.flow["greeting"] == get_template("hello_world").seed.greeting
    # …the Phase 1 owner layer carried by default…
    assert bot.config.flow["translations"] == {"fa": {"greeting": "سلام"}}
    assert bot.config.flow["single_language"] is True
    # …provenance bumped to the applied version, row type synced.
    assert bot.config.flow["template"] == {"id": "hello_world", "version": 1}
    assert bot.bot_type is BotType.HELLO
    invalidate.assert_awaited_once_with("123456789:SECRET_TOKEN")


def test_reclone_preserve_optout_drops_translations(monkeypatch) -> None:
    old_flow = get_template("quiz").seed.model_dump()
    old_flow["translations"] = {"fa": {"fallback_message": "دوباره"}}
    bot = _bot(config_flow=old_flow)
    invalidate = _install(monkeypatch, bot_row=bot)
    _bind_reclone_service(monkeypatch, invalidate)

    resp = client.post(
        "/api/bots/7/reclone",
        json={"template_id": "quiz", "preserve": []},
        headers=_AUTH,
    )

    assert resp.status_code == 200
    # Bare reset: the seed's own translations, not the owner's.
    assert bot.config.flow["translations"] == get_template("quiz").seed.model_dump()["translations"]
    invalidate.assert_awaited_once_with("123456789:SECRET_TOKEN")


def test_reclone_not_owned_404(monkeypatch) -> None:
    invalidate = _install(monkeypatch, bot_row=None)

    resp = client.post(
        "/api/bots/999/reclone",
        json={"template_id": "hello_world"},
        headers=_AUTH,
    )

    assert resp.status_code == 404
    invalidate.assert_not_awaited()


def test_reclone_bad_preserve_key_422(monkeypatch) -> None:
    bot = _bot(config_flow=get_template("hello_world").seed.model_dump())
    bot.bot_type = BotType.HELLO
    invalidate = _install(monkeypatch, bot_row=bot)
    _bind_reclone_service(monkeypatch, invalidate)

    resp = client.post(
        "/api/bots/7/reclone",
        json={"template_id": "hello_world", "preserve": ["menu_buttons"]},
        headers=_AUTH,
    )

    assert resp.status_code == 422
    assert "not preservable" in resp.json()["detail"]["error"]
    invalidate.assert_not_awaited()


def test_reclone_pinned_version(monkeypatch) -> None:
    """``version`` pins the seed — re-clone v1 explicitly when v2 exists."""
    saved = {tid: list(specs) for tid, specs in REGISTRY.items()}
    try:
        dumped = get_template("hello_world", version=1).seed.model_dump()
        dumped["greeting"] = "v2 copy"
        dumped["template"] = {"id": "hello_world", "version": 2}
        register(
            TemplateSpec(
                id="hello_world",
                version=2,
                bot_type=BotType.HELLO,
                display_name="Hello World",
                description="test bump",
                seed=TypeAdapter(BotConfigUnion).validate_python(dumped),
            )
        )
        bot = _bot(config_flow=get_template("hello_world", version=1).seed.model_dump())
        bot.bot_type = BotType.HELLO
        invalidate = _install(monkeypatch, bot_row=bot)
        _bind_reclone_service(monkeypatch, invalidate)

        resp = client.post(
            "/api/bots/7/reclone",
            json={"template_id": "hello_world", "version": 1},
            headers=_AUTH,
        )

        assert resp.status_code == 200
        # Pinned to v1 — NOT the v2 the registry now holds as latest.
        assert bot.config.flow["greeting"] == "Hello there! 👋"
        assert bot.config.flow["template"] == {"id": "hello_world", "version": 1}
        invalidate.assert_awaited_once()
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)

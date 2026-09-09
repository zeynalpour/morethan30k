"""Regression tests for the main controller bot /start + managed-bot flow.

Pins two production bugs:

* ``KeyboardButtonRequestManagedBot()`` crashed on /start because aiogram's
  model requires ``request_id`` — the welcome message was never sent.
* The ``managed_bot`` typed handler: fetches the token via the native
  ``GetManagedBotToken`` method and provisions the bot (replaces the old
  raw-dict path).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import ManagedBotUpdated, User

from tme.database.models import BotType
from tme.routers import main_bot as main_router_module
from tme.routers.main_bot import (
    _create_bot_keyboard,
    _type_picker_keyboard,
    on_managed_bot,
    on_my_bots,
    on_open_settings,
    on_pick_type,
)


def _make_event() -> ManagedBotUpdated:
    """Build a ``ManagedBotUpdated`` for the controller-flow tests."""
    owner = User(id=42, is_bot=False, first_name="Owner", username="owner_user")
    managed = User(id=999, is_bot=True, first_name="TestBot", username="test_bot")
    return ManagedBotUpdated(user=owner, bot_user=managed)


def test_create_bot_keyboard_is_valid() -> None:
    """/start's keyboard must construct without raising."""
    kb = _create_bot_keyboard()
    button = kb.keyboard[0][0]
    assert button.request_managed_bot is not None
    assert button.request_managed_bot.request_id == 1  # required by aiogram
    assert button.text == "➕ Create a Managed Bot"
    # Second row: entry point to the My Bots list (existing bots included).
    assert kb.keyboard[1][0].text == "🤖 My Bots"


def test_managed_bot_handler_asks_for_type(monkeypatch) -> None:
    """The typed handler fetches the token, then asks for a type — no provisioning yet."""
    event = _make_event()
    pending: dict = {}
    monkeypatch.setattr(main_router_module, "_PENDING", pending)

    # ``bot(GetManagedBotToken(...))`` returns the token; send_message is free.
    fake_bot = AsyncMock(return_value="123456789:FAKE_TOKEN")
    provision = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)

    asyncio.run(on_managed_bot(event, fake_bot))

    # The token is held back, not provisioned, until the owner picks a type.
    provision.assert_not_awaited()
    assert pending == {42: "123456789:FAKE_TOKEN"}

    # The owner was sent the type picker.
    fake_bot.send_message.assert_awaited_once()
    send_kwargs = fake_bot.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 42
    picker = send_kwargs["reply_markup"]
    labels = [b.callback_data for row in picker.inline_keyboard for b in row]
    assert labels == ["pick:generic", "pick:hello", "pick:echo"]


def test_type_picker_keyboard_is_valid() -> None:
    """The picker must offer every provisionable behaviour type."""
    kb = _type_picker_keyboard()
    labels = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert labels == ["pick:generic", "pick:hello", "pick:echo"]


def test_pick_type_provisions_with_chosen_type(monkeypatch) -> None:
    """Picking a type provisions the pending bot with it and confirms."""
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="pick:echo",
        answer=AsyncMock(),
    )
    pending = {42: "123456789:FAKE_TOKEN"}
    monkeypatch.setattr(main_router_module, "_PENDING", pending)
    provision = AsyncMock(return_value=SimpleNamespace(id=1, username="echo_bot"))
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)
    fake_bot = AsyncMock()

    asyncio.run(on_pick_type(callback, fake_bot))

    provision.assert_awaited_once()
    call_kwargs = provision.await_args.kwargs
    assert call_kwargs["token"] == "123456789:FAKE_TOKEN"
    assert call_kwargs["owner_telegram_id"] == 42
    assert call_kwargs["bot_type"] is BotType.ECHO
    assert 42 not in pending  # consumed by the pick

    callback.answer.assert_awaited_once()
    fake_bot.send_message.assert_awaited_once()
    send_kwargs = fake_bot.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 42
    assert "@echo_bot" in send_kwargs["text"]


def test_pick_type_without_pending_answers_expired(monkeypatch) -> None:
    """A stale pick (no pending token) must not provision anything."""
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="pick:hello",
        answer=AsyncMock(),
    )
    monkeypatch.setattr(main_router_module, "_PENDING", {})
    provision = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)
    fake_bot = AsyncMock()

    asyncio.run(on_pick_type(callback, fake_bot))

    provision.assert_not_awaited()
    fake_bot.send_message.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_pick_type_isolation_other_owner(monkeypatch) -> None:
    """User B picking must never reach user A's pending token."""
    attacker = SimpleNamespace(
        from_user=SimpleNamespace(id=99, username="b", first_name="B"),
        data="pick:echo",
        answer=AsyncMock(),
    )
    pending = {42: "VICTIM_TOKEN"}
    monkeypatch.setattr(main_router_module, "_PENDING", pending)
    provision = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)
    fake_bot = AsyncMock()

    asyncio.run(on_pick_type(attacker, fake_bot))

    provision.assert_not_awaited()
    fake_bot.send_message.assert_not_awaited()
    attacker.answer.assert_awaited_once()
    assert pending == {42: "VICTIM_TOKEN"}  # untouched


def test_pick_type_provision_failure_restores_token(monkeypatch) -> None:
    """On provisioning failure the token goes back to pending for a re-pick."""
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="pick:hello",
        answer=AsyncMock(),
    )
    pending = {42: "123456789:FAKE_TOKEN"}
    monkeypatch.setattr(main_router_module, "_PENDING", pending)
    provision = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)
    fake_bot = AsyncMock()

    asyncio.run(on_pick_type(callback, fake_bot))

    assert pending == {42: "123456789:FAKE_TOKEN"}  # restored for re-pick
    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.await_args.kwargs["text"]
    assert "Tap your chosen type again" in text


def test_open_settings_issues_dashboard_link(monkeypatch) -> None:
    """Tapping 'Open Settings' issues a token and sends the dashboard link."""
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="settings:7",
        answer=AsyncMock(),
    )
    issue = AsyncMock(return_value="RAWTOKEN")
    monkeypatch.setattr("tme.routers.main_bot.create_dashboard_token_for_owner", issue)
    fake_bot = AsyncMock()

    asyncio.run(on_open_settings(callback, fake_bot))

    issue.assert_awaited_once_with(bot_id=7, owner_telegram_id=42)
    callback.answer.assert_awaited_once()
    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.await_args.kwargs["text"]
    assert "https://test.example.com/dashboard/?t=RAWTOKEN&bid=7" in text


def test_open_settings_rejected_when_bot_not_owned(monkeypatch) -> None:
    """Issuing a token for a bot the user doesn't own is refused."""
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="settings:999",
        answer=AsyncMock(),
    )
    issue = AsyncMock(return_value=None)
    monkeypatch.setattr("tme.routers.main_bot.create_dashboard_token_for_owner", issue)
    fake_bot = AsyncMock()

    asyncio.run(on_open_settings(callback, fake_bot))

    issue.assert_awaited_once()
    fake_bot.send_message.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_my_bots_lists_owner_bots_with_settings_buttons(monkeypatch) -> None:
    """Existing bots get a settings button too — not just newly created ones."""
    message = SimpleNamespace(from_user=SimpleNamespace(id=42))
    fake_bot = AsyncMock()
    bots = [SimpleNamespace(id=1, username="alpha"), SimpleNamespace(id=2, username=None)]
    lst = AsyncMock(return_value=bots)
    monkeypatch.setattr("tme.routers.main_bot.list_bots_for_owner", lst)

    asyncio.run(on_my_bots(message, fake_bot))

    lst.assert_awaited_once_with(owner_telegram_id=42)
    fake_bot.send_message.assert_awaited_once()
    kb = fake_bot.send_message.await_args.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks == ["settings:1", "settings:2"]
    assert labels[0] == "⚙️ @alpha"
    assert labels[1] == "⚙️ Bot #2"  # nameless bot falls back to its id


def test_my_bots_empty_state(monkeypatch) -> None:
    message = SimpleNamespace(from_user=SimpleNamespace(id=42))
    fake_bot = AsyncMock()
    lst = AsyncMock(return_value=[])
    monkeypatch.setattr("tme.routers.main_bot.list_bots_for_owner", lst)

    asyncio.run(on_my_bots(message, fake_bot))

    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.await_args.kwargs["text"]
    assert "don't have any bots" in text

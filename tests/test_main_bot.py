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

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ManagedBotUpdated, User

from tme.database.models import BotType
from tme.routers import main_bot as main_router_module
from tme.routers.main_bot import (
    _create_bot_keyboard,
    _type_picker_keyboard,
    on_dashboard_command,
    on_managed_bot,
    on_my_bots,
    on_pick_type,
    register_main_commands,
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
    # No stored preference, no Telegram language → English copy.
    monkeypatch.setattr("tme.routers.main_bot.get_user_language", AsyncMock(return_value=None))

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


def test_dashboard_command_sends_mini_app_button() -> None:
    """'/dashboard' sends the 'Open Mini App' button for the home page."""
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(on_dashboard_command(message))

    message.answer.assert_awaited_once()
    kb = message.answer.await_args.kwargs["reply_markup"]
    button = kb.inline_keyboard[0][0]
    assert button.text == "🚀 Open Dashboard"
    assert button.web_app is not None
    assert button.web_app.url == "https://test.example.com/dashboard/"


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
    urls = [b.web_app.url for row in kb.inline_keyboard for b in row]
    assert urls == [
        "https://test.example.com/dashboard/?bid=1",
        "https://test.example.com/dashboard/?bid=2",
    ]
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


def test_my_bots_button_filter_matches_emoji_variant() -> None:
    """The reply-keyboard button sends '🤖 My Bots'; the filter must accept it."""
    filt = F.text.in_(["My Bots", "🤖 My Bots", "🤖 ربات‌های من"])
    assert filt.resolve(SimpleNamespace(text="My Bots")) is True
    assert filt.resolve(SimpleNamespace(text="🤖 My Bots")) is True
    assert filt.resolve(SimpleNamespace(text="🤖 ربات‌های من")) is True
    assert filt.resolve(SimpleNamespace(text="Something else")) is False


def test_register_main_commands_sends_command_menu() -> None:
    """Startup pushes /mybots into the bot's command menu automatically."""
    fake_bot = AsyncMock()
    asyncio.run(register_main_commands(fake_bot))
    assert fake_bot.set_my_commands.await_count == 2  # en default + fa scope
    calls = fake_bot.set_my_commands.await_args_list
    commands = calls[0].args[0]
    assert [c.command for c in commands] == ["start", "mybots", "dashboard", "language"]
    # The fa scope carries localized descriptions with the same commands.
    fa_call = [c for c in calls if c.kwargs.get("language_code") == "fa"]
    assert len(fa_call) == 1
    fa_commands = fa_call[0].args[0]
    assert [c.command for c in fa_commands] == ["start", "mybots", "dashboard", "language"]
    assert fa_commands[0].description == "شروع"
    assert commands[0].description == "Start"


def test_register_main_commands_tolerates_api_error() -> None:
    """A Telegram API failure must not crash startup — the menu is cosmetic."""
    fake_bot = AsyncMock()
    fake_bot.set_my_commands.side_effect = TelegramBadRequest(method=None, message="boom")
    asyncio.run(register_main_commands(fake_bot))  # must not raise

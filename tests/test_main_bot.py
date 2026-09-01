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
from unittest.mock import AsyncMock

from aiogram.types import ManagedBotUpdated, User

from tme.routers.main_bot import _create_bot_keyboard, on_managed_bot


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


def test_managed_bot_handler_provisions_via_native_method(monkeypatch) -> None:
    """The typed handler must fetch the token and provision the bot."""
    event = _make_event()

    # ``bot(GetManagedBotToken(...))`` returns the token; send_message is free.
    fake_bot = AsyncMock(return_value="123456789:FAKE_TOKEN")
    provision = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.provision_managed_bot", provision)

    asyncio.run(on_managed_bot(event, fake_bot))

    provision.assert_awaited_once()
    call_kwargs = provision.await_args.kwargs
    assert call_kwargs["token"] == "123456789:FAKE_TOKEN"
    assert call_kwargs["owner_telegram_id"] == 42
    assert call_kwargs["owner_first_name"] == "Owner"
    assert call_kwargs["owner_username"] == "owner_user"

    # The owner was told their bot is live.
    fake_bot.send_message.assert_awaited_once()
    send_kwargs = fake_bot.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == 42
    assert "@test_bot" in send_kwargs["text"]

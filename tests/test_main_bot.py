"""Regression tests for the main controller bot /start flow.

Pins two production bugs:

* ``KeyboardButtonRequestManagedBot()`` crashed on /start because aiogram's
  model requires ``request_id`` — the welcome message was never sent.
* ``handle_managed_bot`` read Telegram's payload under the wrong key
  (``bot`` instead of ``bot_user``), so provisioning silently aborted.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from tme.routers.main_bot import _create_bot_keyboard
from tme.services.managed_bots import handle_managed_bot


def test_create_bot_keyboard_is_valid() -> None:
    """/start's keyboard must construct without raising."""
    kb = _create_bot_keyboard()
    button = kb.keyboard[0][0]
    assert button.request_managed_bot is not None
    assert button.request_managed_bot.request_id == 1  # required by aiogram
    assert button.text == "➕ Create a Managed Bot"


def test_handle_managed_bot_reads_bot_user(monkeypatch) -> None:
    """Telegram's payload uses ``bot_user``; provisioning must receive the ids."""
    fake_bot = AsyncMock()
    provision = AsyncMock()
    monkeypatch.setattr("tme.services.managed_bots.provision_managed_bot", provision)

    payload = {
        "update_id": 1,
        "managed_bot": {
            "user": {"id": 42, "is_bot": False, "first_name": "Owner"},
            "bot_user": {
                "id": 999,
                "is_bot": True,
                "first_name": "TestBot",
                "username": "test_bot",
            },
        },
    }

    asyncio.run(handle_managed_bot(payload, fake_bot))

    provision.assert_awaited_once()
    call_kwargs = provision.await_args.kwargs
    assert call_kwargs["owner_telegram_id"] == 42
    assert call_kwargs["owner_first_name"] == "Owner"

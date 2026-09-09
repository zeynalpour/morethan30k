"""Tests for Phase 1 S1.2 — the /language picker on tenant bots."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from aiogram import F
from aiogram.types import Chat, Message, User

from tme.routers.dynamic import (
    _LANG_PREFIX,
    _language_keyboard,
    on_language_command,
    on_language_pick,
)

_EXPECTED_CODES = ["en", "fa", "de", "ru", "ar", "es", "fr", "tr", "zh", "hi", "id", "pt"]


def test_language_keyboard_offers_expected_flags() -> None:
    kb = _language_keyboard()
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == [f"{_LANG_PREFIX}{c}" for c in _EXPECTED_CODES]


def test_language_command_sends_picker() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(on_language_command(message))

    message.answer.assert_awaited_once()
    kb = message.answer.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "lang:en"


def test_language_pick_persists_choice(monkeypatch) -> None:
    set_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.dynamic.set_user_language", set_lang)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=77, type="private"),
        from_user=User(id=77, is_bot=False, first_name="Tester"),
        text="🌐 Choose your language:",
    )
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        data="lang:fa",
        answer=AsyncMock(),
        message=message,
    )
    with mock.patch.object(Message, "edit_text", new=AsyncMock()) as edit:
        asyncio.run(on_language_pick(callback))

    set_lang.assert_awaited_once_with(77, "fa")
    callback.answer.assert_awaited_once()
    edit.assert_awaited_once()


def test_menu_click_filter_excludes_language_picks() -> None:
    """Menu callbacks and language picks must never collide."""
    filt = F.data & ~F.data.startswith("lang:")
    assert filt.resolve(SimpleNamespace(data="about")) is True
    assert filt.resolve(SimpleNamespace(data="lang:fa")) is False

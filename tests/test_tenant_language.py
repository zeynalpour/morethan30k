"""Tests for Phase 1 S1.2 — the /language picker on tenant bots."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from aiogram import F
from aiogram.types import Chat, Message, User
from pydantic import TypeAdapter

from tme.routers.dynamic import (
    _language_keyboard,
    on_language_command,
    on_language_pick,
)
from tme.schemas.bot_config import BotConfigUnion

_KNOWN_FLAGS = {"en", "fa", "de", "ru", "ar", "es", "fr", "tr", "zh", "hi", "id", "pt"}


def _config(*, single_language: bool = False, translations: dict | None = None) -> BotConfigUnion:
    return TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "generic",
            "single_language": single_language,
            "translations": translations or {},
        }
    )


def test_language_keyboard_lists_only_the_bots_languages_plus_auto() -> None:
    kb = _language_keyboard(_config(translations={"fa": {}, "ru": {}, "zz": {}}))
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["lang:fa", "lang:ru", "lang:zz", "lang:auto"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels[-1] == "🔄 Auto (Telegram)"
    # Unknown codes render as a globe + code instead of a fake flag.
    assert labels[2] == "🌐 zz"


def test_language_keyboard_with_no_translations_offers_only_auto() -> None:
    kb = _language_keyboard(_config())
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["lang:auto"]


def test_language_command_sends_picker_for_translated_languages() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(on_language_command(message, _config(translations={"fa": {}})))

    message.answer.assert_awaited_once()
    kb = message.answer.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "lang:fa"


def test_language_command_refused_on_single_language_bot() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(on_language_command(message, _config(single_language=True)))

    message.answer.assert_awaited_once()
    assert "single-language" in message.answer.await_args.args[0]
    assert "reply_markup" not in message.answer.await_args.kwargs


def test_language_pick_persists_choice(monkeypatch) -> None:
    set_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.dynamic.set_user_language", set_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        data="lang:fa",
        answer=AsyncMock(),
        message=_message(),
    )
    with mock.patch.object(Message, "edit_text", new=AsyncMock()) as edit:
        asyncio.run(on_language_pick(callback, _config(translations={"fa": {}})))

    set_lang.assert_awaited_once_with(77, "fa")
    callback.answer.assert_awaited_once()
    edit.assert_awaited_once()


def test_language_pick_auto_clears_preference(monkeypatch) -> None:
    clear_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.dynamic.clear_user_language", clear_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        data="lang:auto",
        answer=AsyncMock(),
        message=_message(),
    )
    with mock.patch.object(Message, "edit_text", new=AsyncMock()):
        asyncio.run(on_language_pick(callback, _config(translations={"fa": {}})))

    clear_lang.assert_awaited_once_with(77)
    callback.answer.assert_awaited_once()


def test_language_pick_refused_on_single_language_bot(monkeypatch) -> None:
    set_lang = AsyncMock()
    clear_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.dynamic.set_user_language", set_lang)
    monkeypatch.setattr("tme.routers.dynamic.clear_user_language", clear_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        data="lang:fa",
        answer=AsyncMock(),
        message=_message(),
    )

    asyncio.run(on_language_pick(callback, _config(single_language=True)))

    set_lang.assert_not_awaited()
    clear_lang.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_menu_click_filter_excludes_language_picks() -> None:
    """Menu callbacks and language picks must never collide."""
    filt = F.data & ~F.data.startswith("lang:")
    assert filt.resolve(SimpleNamespace(data="about")) is True
    assert filt.resolve(SimpleNamespace(data="lang:fa")) is False
    assert filt.resolve(SimpleNamespace(data="lang:auto")) is False


def _message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=77, type="private"),
        from_user=User(id=77, is_bot=False, first_name="Tester"),
        text="🌐 Choose your language:",
    )

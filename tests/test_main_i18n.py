"""Phase 1 S1.3 — controller-bot copy localization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from aiogram.types import Chat, ManagedBotUpdated, Message, User

from tme.core.main_i18n import MAIN_BOT_STRINGS, supported_languages, tr
from tme.routers import main_bot as module
from tme.routers.main_bot import (
    _language_keyboard,
    on_language_command,
    on_language_pick,
    on_managed_bot,
    on_pick_type,
)


def _make_event(language_code: str | None = None) -> ManagedBotUpdated:
    owner = User(
        id=42,
        is_bot=False,
        first_name="Owner",
        username="owner_user",
        language_code=language_code,
    )
    managed = User(id=999, is_bot=True, first_name="TestBot", username="test_bot")
    return ManagedBotUpdated(user=owner, bot_user=managed)


def test_tr_falls_back_to_english_for_unknown_language() -> None:
    assert tr("xx", "start.welcome", name="Saeed") == (
        "👋 Hi Saeed! Welcome to TME.\n\nTap the button below to create your own Telegram bot."
    )
    assert tr(None, "cmd.start") == "Start"


def test_every_language_defines_every_key() -> None:
    en = MAIN_BOT_STRINGS["en"]
    for code, table in MAIN_BOT_STRINGS.items():
        assert table.keys() == en.keys(), f"{code} table drifted from en"


def test_fa_copy_is_translated() -> None:
    assert "خوش آمدی" in tr("fa", "start.welcome", name="سعید")
    assert tr("fa", "cmd.mybots") == "ربات‌های من و تنظیمات"
    assert "ربات" in tr("fa", "bot.fallback_name", id=2)
    assert supported_languages() == ["en", "fa"]


def test_managed_bot_speaks_owner_language(monkeypatch) -> None:
    """A fa-preferring owner gets the whole creation flow in Persian."""
    event = _make_event(language_code="en")  # Telegram UI English…
    monkeypatch.setattr(module, "_PENDING", {})
    monkeypatch.setattr(
        "tme.routers.main_bot.get_user_language", AsyncMock(return_value="fa")
    )  # …but an explicit /language choice wins.
    fake_bot = AsyncMock(return_value="123456789:FAKE_TOKEN")

    asyncio.run(on_managed_bot(event, fake_bot))

    send_kwargs = fake_bot.send_message.await_args.kwargs
    assert "ربات جدیدت آماده است" in send_kwargs["text"]
    labels = [b.text for row in send_kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["🧩 عمومی", "👋 سلام", "🔁 تکرار"]


def test_pick_type_confirm_in_owner_language(monkeypatch) -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner_user", first_name="Owner"),
        data="pick:echo",
        answer=AsyncMock(),
    )
    monkeypatch.setattr(module, "_PENDING", {42: "123456789:FAKE_TOKEN"})
    monkeypatch.setattr(
        "tme.routers.main_bot.provision_managed_bot",
        AsyncMock(return_value=SimpleNamespace(id=1, username="echo_bot")),
    )
    fake_bot = AsyncMock()

    asyncio.run(on_pick_type(callback, fake_bot, language_code="fa"))

    text = fake_bot.send_message.await_args.kwargs["text"]
    assert "ساخته شد" in text and "@echo_bot" in text
    button = fake_bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "⚙️ باز کردن تنظیمات"


def test_language_command_sends_picker() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(on_language_command(message))

    kb = message.answer.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["lang:en", "lang:fa", "lang:auto"]


def test_language_picker_lists_only_supported_languages() -> None:
    kb = _language_keyboard()
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == ["🇬🇧 English", "🇮🇷 فارسی", "🔄 Auto (Telegram)"]


def _msg() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        text="🌐 Choose your language:",
    )


def test_language_pick_persists_and_edits(monkeypatch) -> None:
    set_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.set_user_language", set_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="lang:fa",
        answer=AsyncMock(),
        message=_msg(),
    )
    with mock.patch.object(Message, "edit_text", new=AsyncMock()) as edit:
        asyncio.run(on_language_pick(callback))

    set_lang.assert_awaited_once_with(42, "fa")
    callback.answer.assert_awaited_once()
    edit.assert_awaited_once_with("🌐 Language: 🇮🇷 فارسی")


def test_language_pick_auto_clears(monkeypatch) -> None:
    clear_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.clear_user_language", clear_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="lang:auto",
        answer=AsyncMock(),
        message=_msg(),
    )
    with mock.patch.object(Message, "edit_text", new=AsyncMock()) as edit:
        asyncio.run(on_language_pick(callback))

    clear_lang.assert_awaited_once_with(42)
    edit.assert_awaited_once_with("🌐 Language: 🔄 Auto (Telegram)")


def test_language_pick_rejects_unknown_code(monkeypatch) -> None:
    set_lang = AsyncMock()
    monkeypatch.setattr("tme.routers.main_bot.set_user_language", set_lang)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="lang:xx",
        answer=AsyncMock(),
        message=None,
    )

    asyncio.run(on_language_pick(callback))

    set_lang.assert_not_awaited()
    callback.answer.assert_awaited_once()

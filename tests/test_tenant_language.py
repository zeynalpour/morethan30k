"""Tests for Phase 1 S1.2 — the /language picker on tenant bots."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from aiogram import F
from aiogram.types import CallbackQuery, Chat, Message, User
from pydantic import TypeAdapter

from tme.routers.dynamic import (
    _language_keyboard,
    on_language_command,
    on_language_pick,
    on_step_option,
    on_step_start,
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


# --------------------------------------------------------------------------- #
# Issue #23 — the router hands the resolved language to the shared step engine
# --------------------------------------------------------------------------- #
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _FakeBot:
    """Records outgoing step prompts; stands in for aiogram's Bot."""

    def __init__(self, bot_id: int = 555) -> None:
        self.id = bot_id
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent.append(text)


def _step_config(*, main_language: str | None = "fa"):
    return TypeAdapter(BotConfigUnion).validate_python(
        {
            "bot_type": "generic",
            "active_modules": ["steps"],
            "main_language": main_language,
            "steps": [
                {
                    "id": "rating",
                    "prompt": "How would you rate us? ⭐",
                    "options": [{"label": "😍 Excellent", "value": "excellent"}],
                },
                {"id": "comment", "prompt": "Anything to add? 💬", "answer_type": "free_text"},
            ],
            "translations": {
                "fa": {
                    "steps": {
                        "rating": {
                            "prompt": "به ما چه امتیازی می‌دهید؟ ⭐",
                            "options": ["😍 عالی"],
                        },
                        "comment": {"prompt": "چیزی برای اضافه کردن دارید؟ 💬"},
                    }
                }
            },
        }
    )


def _callback(data: str, bot, message: Message | None = None) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=77, is_bot=False, first_name="Tester"),
        chat_instance="c",
        data=data,
        message=(message or _message()).as_(bot),
    )


def test_step_start_prompt_is_rendered_in_the_users_language(monkeypatch) -> None:
    """on_step_start → start_flow_at must carry ``language_code`` (issue #23)."""
    monkeypatch.setattr("tme.services.steps.redis_client", _FakeRedis())
    bot = _FakeBot()

    with mock.patch.object(CallbackQuery, "answer", new=AsyncMock()):
        asyncio.run(on_step_start(_callback("step:0", bot), _step_config(), "fa"))

    assert bot.sent == ["به ما چه امتیازی می‌دهید؟ ⭐"]


def test_step_option_advances_with_localized_copy(monkeypatch) -> None:
    """The next step's prompt is localized too — same seam, engine-side."""
    monkeypatch.setattr("tme.services.steps.redis_client", _FakeRedis())
    bot = _FakeBot()
    config = _step_config()

    with mock.patch.object(CallbackQuery, "answer", new=AsyncMock()):
        asyncio.run(on_step_start(_callback("step:0", bot), config, "fa"))
        asyncio.run(on_step_option(_callback("stepopt:0:0", bot), config, "fa"))

    assert bot.sent == ["به ما چه امتیازی می‌دهید؟ ⭐", "چیزی برای اضافه کردن دارید؟ 💬"]


def test_step_start_without_a_language_renders_the_base_copy(monkeypatch) -> None:
    """Unset language + no main_language → the base flow (today's behaviour).

    With main_language="fa" the Persian step copy IS the right answer for a
    user with no language, so this pins the un-declared case explicitly.
    """
    monkeypatch.setattr("tme.services.steps.redis_client", _FakeRedis())
    config = TypeAdapter(BotConfigUnion).validate_python(
        {**_step_config().model_dump(), "main_language": None}
    )
    bot = _FakeBot()

    with mock.patch.object(CallbackQuery, "answer", new=AsyncMock()):
        asyncio.run(on_step_start(_callback("step:0", bot), config, None))

    assert bot.sent == ["How would you rate us? ⭐"]


def test_step_start_uses_the_main_language_when_no_user_language_is_known(
    monkeypatch,
) -> None:
    """The main language is the middle layer — it answers for unknown users."""
    monkeypatch.setattr("tme.services.steps.redis_client", _FakeRedis())
    bot = _FakeBot()

    with mock.patch.object(CallbackQuery, "answer", new=AsyncMock()):
        asyncio.run(on_step_start(_callback("step:0", bot), _step_config(), None))

    assert bot.sent == ["به ما چه امتیازی می‌دهید؟ ⭐"]


def _message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=77, type="private"),
        from_user=User(id=77, is_bot=False, first_name="Tester"),
        text="🌐 Choose your language:",
    )

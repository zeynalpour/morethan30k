"""Tests for the tenant I18nMiddleware (Phase 1 S1.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, User

from tme.middlewares.i18n_middleware import I18nMiddleware


def _message(user_id: int, language_code: str | None) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester", language_code=language_code),
        text="hi",
    )


async def _run(
    monkeypatch, *, stored, telegram_code, single_language=False
) -> tuple[dict, AsyncMock]:
    get_lang = AsyncMock(return_value=stored)
    monkeypatch.setattr("tme.middlewares.i18n_middleware.get_user_language", get_lang)
    bot_config = SimpleNamespace(single_language=single_language)
    data: dict = {"bot_config": bot_config}

    async def handler(_event, _data) -> None:
        return None

    await I18nMiddleware()(handler, _message(7, telegram_code), data)
    return data, get_lang


def test_middleware_preference_wins_over_telegram(monkeypatch) -> None:
    data, get_lang = asyncio.run(_run(monkeypatch, stored="fa", telegram_code="en-US"))
    assert data["language_code"] == "fa"
    get_lang.assert_awaited_once_with(7)


def test_middleware_uses_telegram_code_without_preference(monkeypatch) -> None:
    data, _ = asyncio.run(_run(monkeypatch, stored=None, telegram_code="en-US"))
    assert data["language_code"] == "en"


def test_middleware_skips_lookup_for_single_language_bots(monkeypatch) -> None:
    data, get_lang = asyncio.run(
        _run(monkeypatch, stored="de", telegram_code="fa", single_language=True)
    )
    assert data["language_code"] is None
    get_lang.assert_not_awaited()

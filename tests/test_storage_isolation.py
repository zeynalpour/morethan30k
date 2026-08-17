"""Tests for multi-tenant FSM key isolation (spec §3.3).

The whole platform's correctness rests on two users under different bots never
sharing FSM state. These tests pin that guarantee.
"""

from __future__ import annotations

from aiogram.fsm.storage.base import StorageKey

from tme.core.storage import storage


def _key(bot_id: int, chat_id: int, user_id: int) -> str:
    return storage.key_builder.build(StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id))


def test_key_embeds_bot_and_user() -> None:
    key = _key(bot_id=999, chat_id=111, user_id=222)
    assert key.startswith("state:")
    assert "999" in key  # bot id present
    assert "222" in key  # user id present


def test_same_user_different_bots_never_collide() -> None:
    a = _key(bot_id=1, chat_id=5, user_id=42)
    b = _key(bot_id=2, chat_id=5, user_id=42)
    assert a != b, "same user under two bots must map to distinct FSM keys"


def test_same_bot_different_users_never_collide() -> None:
    a = _key(bot_id=7, chat_id=5, user_id=100)
    b = _key(bot_id=7, chat_id=5, user_id=200)
    assert a != b

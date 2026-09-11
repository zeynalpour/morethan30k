"""End-to-end Telegram userbot tests (dev-only).

Sends a real message to the controller bot as a real user (Pyrogram
userbot) and asserts the bot responds. These tests only run when
``APP_ENV=dev`` AND the userbot credentials are present; every other
environment skips the module — the test server and production rely on
human manual verification instead (tracked on GitHub by the QA agent).

Required environment (dev only):

* ``MAIN_BOT_TOKEN``            — controller bot token (same one the stack uses)
* ``TELEGRAM_API_ID``           — Telegram app id (my.telegram.org)
* ``TELEGRAM_API_HASH``         — Telegram app hash
* ``TELEGRAM_STRING_SESSION``   — Pyrogram session string of the dev userbot

Environment guard: ``APP_ENV`` must be exactly ``dev`` for this module to
collect. CI sets ``APP_ENV=test`` so the suite is skipped there.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

# 1. ENV GUARD: skip entire module unless running in 'dev'.
APP_ENV = os.getenv("APP_ENV", "").lower()
if APP_ENV != "dev":
    pytest.skip(
        f"Skipping Telegram E2E Userbot tests (APP_ENV={APP_ENV or 'unset'} != dev).",
        allow_module_level=True,
    )

# 2. LOAD CREDENTIALS (module level, after the guard).
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_STRING = os.getenv("TELEGRAM_STRING_SESSION")

# Imported only when the env guard passed — pyrogram (kurigram) is a dev
# extra, so test/prod environments must not even import it.
from pyrogram import Client  # noqa: E402

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all((MAIN_BOT_TOKEN, API_ID, API_HASH, SESSION_STRING)),
        reason="dev userbot credentials incomplete "
        "(MAIN_BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_STRING_SESSION)",
    ),
]


def get_target_bot_username(bot_token: str) -> str:
    """Dynamically fetch the bot's username using Telegram's getMe API."""
    if not bot_token:
        raise ValueError("MAIN_BOT_TOKEN is missing from environment variables.")

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    data = response.json()

    if data.get("ok"):
        return f"@{data['result']['username']}"
    raise ValueError(f"Failed to fetch bot info from Telegram API: {data}")


async def test_bot_start_command():
    assert MAIN_BOT_TOKEN, "MAIN_BOT_TOKEN missing (dev env)"
    assert API_ID and API_HASH and SESSION_STRING, "userbot credentials incomplete"

    target_bot = get_target_bot_username(MAIN_BOT_TOKEN)

    async with Client(
        "dev_userbot",
        api_id=int(API_ID),
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    ) as app:
        sent_msg = await app.send_message(target_bot, "/start")
        # Give the controller bot (webhook round-trip) a moment to reply.
        await asyncio.sleep(2)

        reply = None
        async for message in app.get_chat_history(target_bot, limit=1):
            reply = message
            break

        assert reply is not None, f"Bot {target_bot} did not respond to /start"
        assert reply.id != sent_msg.id, f"Bot {target_bot} did not respond to /start"
        assert reply.text, f"Bot {target_bot} sent an empty response"

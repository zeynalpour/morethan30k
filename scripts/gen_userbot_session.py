"""Generate a Pyrogram string session for the dev userbot E2E test.

Run once on the dev box (APP_ENV=dev), interactively:

    uv run python scripts/gen_userbot_session.py

You need api_id / api_hash from https://my.telegram.org (API development
tools). The script asks for the phone number and the login code Telegram
sends you, then prints the ``TELEGRAM_STRING_SESSION`` value to put in
``.env``. The session belongs to whatever *user* account you log into —
it must NOT be the controller bot itself.

Requires kurigram (dev extra) — install with ``uv sync --extra dev``.
"""

from __future__ import annotations

import asyncio
import os
import sys

try:
    from pyrogram import Client
except ImportError:  # pragma: no cover - guidance for a fresh box
    print("pyrogram (kurigram) not installed — run: uv sync --extra dev", file=sys.stderr)
    raise


def _prompt_for_missing() -> tuple[str, str]:
    """Ask for api credentials synchronously (env values win if present)."""
    api_id = os.getenv("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH") or input("TELEGRAM_API_HASH: ").strip()
    return api_id, api_hash


async def main() -> None:
    api_id, api_hash = _prompt_for_missing()

    print("Logging in — Telegram will send a login code to your account.")
    print("The session is saved to dev_userbot.session next to the script.")
    async with Client("dev_userbot", api_id=int(api_id), api_hash=api_hash) as app:
        session_string = await app.export_session_string()

    print("\nAdd this to .env as TELEGRAM_STRING_SESSION:\n")
    print(session_string)


if __name__ == "__main__":
    asyncio.run(main())

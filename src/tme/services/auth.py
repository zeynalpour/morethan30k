"""Telegram WebApp (Mini App) authentication.

The dashboard is opened as a Telegram Mini App: Telegram injects ``initData``
into the WebView, signed with the bot token. We verify that signature here —
no bearer tokens in URLs, no shared secrets in the browser; the caller's
identity comes straight from Telegram.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from tme.config import settings

#: How old an ``auth_date`` may be before the session is rejected.
_INIT_DATA_MAX_AGE_SECONDS = 24 * 3600


def validate_telegram_init_data(init_data: str | None) -> int | None:
    """Validate Telegram WebApp ``initData`` and return the user's telegram id.

    Returns ``None`` for missing, tampered, expired, or malformed payloads.
    """
    if not init_data:
        return None
    pairs = _verified_pairs(init_data)
    if pairs is None:
        return None

    try:
        user = json.loads(pairs["user"])
    except (KeyError, ValueError, TypeError):
        return None
    user_id = user.get("id")
    return user_id if isinstance(user_id, int) else None


def _verified_pairs(init_data: str) -> dict[str, str] | None:
    """Parse ``initData``; return its fields iff signature and age check out.

    Signature scheme (official Bot API spec):
    * ``secret_key = HMAC_SHA256(key=b"WebAppData", msg=bot_token)``
    * ``data_check_string`` = sorted ``k=v`` pairs (hash excluded), ``\\n``-joined
    * expected ``hash`` = ``HMAC_SHA256(key=secret_key, msg=data_check_string)``
    """
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    bot_token = settings.main_bot_token.get_secret_value()
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    age = time.time() - auth_date
    if age < 0 or age > _INIT_DATA_MAX_AGE_SECONDS:
        return None

    return pairs

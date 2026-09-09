"""Tests for Telegram WebApp initData validation (mini-app auth)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from tme.config import settings
from tme.services.auth import validate_telegram_init_data


def _sign_init_data(*, user_id: int, auth_date: int, tamper_hash: bool = False) -> str:
    """Build initData exactly as Telegram does (HMAC with the bot token)."""
    bot_token = settings.main_bot_token.get_secret_value()
    payload = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Owner"}),
    }
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    payload["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if tamper_hash:
        payload["hash"] = "0" * 64
    return urlencode(payload)


def test_valid_init_data_returns_user_id() -> None:
    init_data = _sign_init_data(user_id=42, auth_date=int(time.time()))
    assert validate_telegram_init_data(init_data) == 42


def test_missing_init_data_is_rejected() -> None:
    assert validate_telegram_init_data(None) is None
    assert validate_telegram_init_data("") is None


def test_tampered_hash_is_rejected() -> None:
    init_data = _sign_init_data(user_id=42, auth_date=int(time.time()), tamper_hash=True)
    assert validate_telegram_init_data(init_data) is None


def test_missing_hash_is_rejected() -> None:
    init_data = urlencode({"auth_date": str(int(time.time()))})
    assert validate_telegram_init_data(init_data) is None


def test_expired_init_data_is_rejected() -> None:
    init_data = _sign_init_data(user_id=42, auth_date=int(time.time()) - 25 * 3600)
    assert validate_telegram_init_data(init_data) is None


def test_future_init_data_is_rejected() -> None:
    init_data = _sign_init_data(user_id=42, auth_date=int(time.time()) + 3600)
    assert validate_telegram_init_data(init_data) is None


def test_missing_user_is_rejected() -> None:
    bot_token = settings.main_bot_token.get_secret_value()
    payload = {"auth_date": str(int(time.time()))}
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    payload["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    assert validate_telegram_init_data(urlencode(payload)) is None

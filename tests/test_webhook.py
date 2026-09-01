"""Tests for the universal webhook gateway's security gate and routing.

We deliberately avoid constructing real aiogram ``Update`` objects or touching
the network: these tests target the logic *we* wrote — the secret-token gate
and the controller/tenant routing branch.

Note: ``TestClient(app)`` is created WITHOUT the ``with`` context manager on
purpose, so FastAPI's lifespan (which would call ``set_webhook`` over the
network) never runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tme import main
from tme.config import settings

client = TestClient(main.app)

_SECRET = settings.webhook_secret.get_secret_value()
_MAIN_TOKEN = settings.main_bot_token.get_secret_value()
_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_rejects_wrong_secret() -> None:
    resp = client.post(
        f"/webhook/{_MAIN_TOKEN}",
        json={"update_id": 1},
        headers={_HEADER: "definitely-wrong"},
    )
    assert resp.status_code == 403


def test_webhook_rejects_missing_secret() -> None:
    resp = client.post(f"/webhook/{_MAIN_TOKEN}", json={"update_id": 1})
    assert resp.status_code == 403


def test_managed_bot_update_flows_through_dispatcher(monkeypatch) -> None:
    """A managed_bot payload on the main bot must reach main_dp (typed path)."""
    feed_update = AsyncMock()
    monkeypatch.setattr("tme.main.main_dp.feed_update", feed_update)

    payload = {
        "update_id": 10,
        "managed_bot": {
            "user": {
                "id": 42,
                "is_bot": False,
                "first_name": "Owner",
            },
            "bot_user": {
                "id": 999,
                "is_bot": True,
                "first_name": "TestBot",
                "username": "test_bot",
            },
        },
    }
    resp = client.post(
        f"/webhook/{_MAIN_TOKEN}",
        json=payload,
        headers={_HEADER: _SECRET},
    )

    assert resp.status_code == 200
    feed_update.assert_awaited_once()


def test_processing_errors_still_return_200(monkeypatch) -> None:
    """A handler blowing up must not make us return 5xx (Telegram would retry)."""
    boom = AsyncMock(side_effect=RuntimeError("kaboom"))
    monkeypatch.setattr("tme.main.main_dp.feed_update", boom)

    resp = client.post(
        f"/webhook/{_MAIN_TOKEN}",
        json={"update_id": 11, "message": {"text": "hi"}},
        headers={_HEADER: _SECRET},
    )
    assert resp.status_code == 200

"""TME universal webhook gateway (FastAPI).

A single endpoint — ``POST /webhook/{bot_token}`` — receives updates for the
controller bot **and** for all 30k+ tenant bots. It:

1. Verifies Telegram's secret-token header.
2. Routes by token: the controller token → ``main_dp``; anything else →
   the shared ``tenant_dp`` (via an on-demand :class:`~aiogram.Bot` from the
   registry).
3. Special-cases the undocumented ``managed_bot_updated`` update, handing it to
   the provisioning service directly.

Design choice — we ``await`` update processing *before* returning ``200``.
Telegram sends the next update for a chat only after receiving our ``200`` (or a
timeout), so awaiting preserves per-chat ordering, which FSM correctness relies
on. Handlers are intentionally light to keep the ack fast.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hmac

from aiogram.types import Update
from fastapi import FastAPI, Header, Path, Request, Response
from fastapi.responses import ORJSONResponse

from tme.config import settings
from tme.core.bot_registry import close_registry, get_tenant_bot, main_bot
from tme.core.dispatchers import main_dp, tenant_dp
from tme.core.logging import configure_logging, get_logger
from tme.core.redis_client import close_redis
from tme.database.engine import dispose_engine
from tme.services.managed_bots import handle_managed_bot_updated

logger = get_logger(__name__)

_MAIN_BOT_TOKEN = settings.main_bot_token.get_secret_value()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown: register the controller webhook, tidy up on exit."""
    configure_logging()
    logger.info("TME gateway starting up")
    try:
        await main_bot.set_webhook(
            url=settings.webhook_url_for(_MAIN_BOT_TOKEN),
            secret_token=settings.webhook_secret.get_secret_value(),
            drop_pending_updates=True,
        )
        logger.info("Controller webhook registered")
    except Exception:
        logger.exception("Failed to register controller webhook (continuing)")

    yield

    logger.info("TME gateway shutting down")
    await close_registry()
    await close_redis()
    await dispose_engine()


app = FastAPI(
    title="TME — Telegram Multi-Tenant Bot Engine",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)


def _verify_secret(header_value: str | None) -> bool:
    """Constant-time check of Telegram's secret-token header."""
    if header_value is None:
        return False
    return hmac.compare_digest(header_value, settings.webhook_secret.get_secret_value())


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/webhook/{bot_token}", tags=["telegram"])
async def telegram_webhook(
    request: Request,
    bot_token: str = Path(..., description="The bot token this update is for."),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Receive and dispatch a single Telegram update for ``bot_token``."""
    if not _verify_secret(x_telegram_bot_api_secret_token):
        logger.warning("Rejected update with bad secret for …%s", bot_token[-6:])
        # 403 — but do not echo detail to unauthenticated callers.
        return ORJSONResponse({"ok": False}, status_code=403)

    data = await request.json()

    try:
        if hmac.compare_digest(bot_token, _MAIN_BOT_TOKEN):
            # --- Controller bot -------------------------------------------
            # Undocumented update type: handled out-of-band from the raw dict.
            if "managed_bot_updated" in data:
                await handle_managed_bot_updated(data, main_bot)
                return ORJSONResponse({"ok": True})

            update = Update.model_validate(data, context={"bot": main_bot})
            await main_dp.feed_update(bot=main_bot, update=update)
        else:
            # --- Tenant bot -----------------------------------------------
            bot = get_tenant_bot(bot_token)
            update = Update.model_validate(data, context={"bot": bot})
            await tenant_dp.feed_update(bot=bot, update=update)
    except Exception:
        # Log and still 200: returning 5xx makes Telegram retry the same broken
        # update indefinitely. The error is captured for us to inspect.
        logger.exception("Error processing update for …%s", bot_token[-6:])

    return ORJSONResponse({"ok": True})


def run() -> None:
    """Console entrypoint (``uv run tme-api``) — launch uvicorn."""
    import uvicorn  # noqa: PLC0415 - deferred so importing the app never pulls in the server

    uvicorn.run(
        "tme.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()

"""Dashboard auth tokens — short-lived bearer credentials for the settings UI.

The Main Bot issues a token when an owner opens settings for one of their
bots; the owner opens the dashboard URL with ``?t=<token>&bid=<bot_id>`` and
the settings API validates it on every request.

Security model:

* Only the SHA-256 **hash** of the token is stored — the raw token exists
  exactly once, in the link sent to the owner's private chat with the Main
  Bot. (Full encryption of bot tokens lands in S0.3; hashing here is free.)
* A token is bound to ``owner_telegram_id`` + an initial ``bot_id``, and the
  API layer authorizes every request against the owner id — so a valid token
  scopes to everything *that owner* owns, never beyond.
* Tokens are short-lived (``TOKEN_TTL``) and never logged.
* ``used_at`` records first use for audit; the token stays valid until it
  expires so the mini app can switch between the owner's bots in one session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets

from sqlalchemy import select

from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, DashboardAuthToken, User

logger = get_logger(__name__)

#: How long a dashboard link stays valid after issuance.
TOKEN_TTL = timedelta(minutes=15)


def _hash(token: str) -> str:
    """SHA-256 hex digest — the only representation of a token we store."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_dashboard_token_for_owner(
    *,
    bot_id: int,
    owner_telegram_id: int,
) -> str | None:
    """Issue a dashboard token for ``bot_id`` iff the user owns that bot.

    Returns the raw token (sent to the owner as a link), or ``None`` if the
    bot does not exist or is owned by someone else.
    """
    token = secrets.token_urlsafe(32)
    async with session_scope() as session:
        result = await session.execute(
            select(BotModel)
            .join(BotModel.owner)
            .where(BotModel.id == bot_id, User.telegram_id == owner_telegram_id)
        )
        bot = result.scalar_one_or_none()
        if bot is None:
            return None
        session.add(
            DashboardAuthToken(
                token_hash=_hash(token),
                bot_id=bot_id,
                owner_telegram_id=owner_telegram_id,
                expires_at=datetime.now(UTC) + TOKEN_TTL,
            )
        )
    logger.info("Issued dashboard token for bot id=%s (owner tg=%s)", bot_id, owner_telegram_id)
    return token


async def list_bots_for_owner(owner_telegram_id: int) -> list[BotModel]:
    """Every bot owned by a Telegram user, newest first (for the My Bots list)."""
    async with session_scope() as session:
        result = await session.execute(
            select(BotModel)
            .join(BotModel.owner)
            .where(User.telegram_id == owner_telegram_id)
            .order_by(BotModel.created_at.desc())
        )
        return list(result.scalars())


async def validate_dashboard_token(token: str) -> DashboardAuthToken | None:
    """Return the token row if valid (exists + unexpired); record first use.

    ``None`` for an unknown, expired, or malformed token — callers treat it as
    authentication failure. ``used_at`` is stamped on first successful use.
    """
    async with session_scope() as session:
        result = await session.execute(
            select(DashboardAuthToken).where(DashboardAuthToken.token_hash == _hash(token))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if row.expires_at < datetime.now(UTC):
            logger.debug("Rejected expired dashboard token for bot id=%s", row.bot_id)
            return None
        if row.used_at is None:
            row.used_at = datetime.now(UTC)
        return row

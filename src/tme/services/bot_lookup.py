"""Token → tenant row resolution (S0.3 activation, issue #28).

Hashing happens at the boundary: callers hold the **raw** token (it is what
Telegram put in the webhook URL), and this module turns it into the routing
key. The order is deliberate and lives in exactly one place:

1. ``bots.token_hash`` — the S0.3 routing key. Indexed, unique, computed with
   the server pepper, and requiring no decryption at all.
2. ``bots.token`` — the **transitional fallback**, so rows the backfill has
   not touched yet keep resolving during the rollout.

The fallback is removed together with the column, once every stack is
backfilled (see ``scripts/vault_backfill.py`` and
docs/architecture/01-data-model.md § Secrets).
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.core.logging import get_logger
from tme.database.models import Bot
from tme.services.vault import token_hash

logger = get_logger(__name__)


def _by_hash(bot_token: str, *, active_only: bool) -> Select[tuple[Bot]]:
    """Statement matching the peppered hash of ``bot_token``."""
    stmt = select(Bot).where(Bot.token_hash == token_hash(bot_token))
    return stmt.where(Bot.is_active.is_(True)) if active_only else stmt


def _by_plaintext(bot_token: str, *, active_only: bool) -> Select[tuple[Bot]]:
    """Statement matching the transitional plaintext column (rollout only)."""
    stmt = select(Bot).where(Bot.token == bot_token)
    return stmt.where(Bot.is_active.is_(True)) if active_only else stmt


async def find_bot_by_token(
    session: AsyncSession, bot_token: str, *, active_only: bool = False
) -> Bot | None:
    """Return the :class:`~tme.database.models.Bot` for a raw token, or ``None``.

    ``active_only`` mirrors the runtime rule (only active bots serve traffic)
    and is used by the config cache; provisioning resolves inactive rows too,
    so a re-provisioned bot is updated in place instead of duplicated.
    """
    bot = (await session.execute(_by_hash(bot_token, active_only=active_only))).scalar_one_or_none()
    if bot is not None:
        return bot

    # Transitional: rows provisioned before S0.3 (or not yet backfilled) have
    # no hash. Never reached once every stack is backfilled.
    bot = (
        await session.execute(_by_plaintext(bot_token, active_only=active_only))
    ).scalar_one_or_none()
    if bot is not None:
        logger.debug("Resolved bot id=%s via the transitional plaintext token", bot.id)
    return bot

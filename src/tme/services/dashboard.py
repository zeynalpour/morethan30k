"""Owner-scoped helpers for the bot-settings dashboard.

Authentication is handled by :mod:`tme.services.auth` (Telegram WebApp
``initData`` validation); this module only answers "which bots does this
owner have?".
"""

from __future__ import annotations

from sqlalchemy import select

from tme.database.engine import session_scope
from tme.database.models import Bot as BotModel, User


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

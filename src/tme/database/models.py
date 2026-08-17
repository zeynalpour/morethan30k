"""ORM models: :class:`User`, :class:`Bot`, :class:`BotConfig`.

Relationships::

    User  1 ─── * Bot  1 ─── 1 BotConfig

A ``User`` (a person talking to the Main Bot) owns any number of ``Bot`` rows
(their cloned tenant bots). Each ``Bot`` has exactly one ``BotConfig`` holding
the JSON flow that the dynamic router executes at runtime.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tme.database.base import Base


class User(Base):
    """A person who interacts with the Main Bot to manage their own bots."""

    __tablename__ = "users"

    #: Telegram user id (natural key). Surrogate ``id`` comes from ``Base``.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: All tenant bots owned by this user.
    bots: Mapped[list[Bot]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id} @{self.username}>"


class Bot(Base):
    """A single tenant (cloned) bot served by the universal webhook.

    ``token`` is the routing key: it appears in ``POST /webhook/{bot_token}`` and
    is used to look the tenant up in the Redis cache / DB.

    .. warning::
       For the MVP the bot token is stored in plaintext. In production it should
       be encrypted at rest (e.g. app-level envelope encryption or ``pgcrypto``).
    """

    __tablename__ = "bots"

    #: The tenant bot's Bot API token — unique routing identifier.
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    #: The bot's own Telegram user id (the numeric prefix of the token).
    telegram_bot_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Owner (the User who created this bot via the Main Bot).
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner: Mapped[User] = relationship(back_populates="bots", lazy="joined")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Whether Telegram's setWebhook has been registered for this token.
    webhook_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: One-to-one configuration holding the JSON flow.
    config: Mapped[BotConfig] = relationship(
        back_populates="bot",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Bot id={self.id} tg={self.telegram_bot_id} @{self.username} active={self.is_active}>"
        )


class BotConfig(Base):
    """The JSON flow / behaviour configuration for one :class:`Bot`.

    ``flow`` is stored as JSONB and validated against
    :class:`tme.schemas.bot_config.BotConfigSchema` at the service layer.
    """

    __tablename__ = "bot_configs"

    bot_id: Mapped[int] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    bot: Mapped[Bot] = relationship(back_populates="config")

    #: Free-form JSON flow (welcome_message, menu_buttons, active_modules, ...).
    flow: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Optional human note / description shown in the management UI.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<BotConfig id={self.id} bot_id={self.bot_id}>"

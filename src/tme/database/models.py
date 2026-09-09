"""ORM models: :class:`User`, :class:`Bot`, :class:`BotConfig`.

Relationships::

    User  1 ─── * Bot  1 ─── 1 BotConfig

A ``User`` (a person talking to the Main Bot) owns any number of ``Bot`` rows
(their cloned tenant bots). Each ``Bot`` has exactly one ``BotConfig`` holding
the JSON flow that the dynamic router executes at runtime.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tme.database.base import Base


class BotType(StrEnum):
    """Typed bot categories — the flow engine dispatches per kind.

    Each value maps to a dedicated config variant (see
    :mod:`tme.schemas.bot_config`). The enum lives here because it is a
    database concern (the ``bots.bot_type`` column), but it is intentionally
    importable from the schema layer too.
    """

    GENERIC = "generic"
    HELLO = "hello"
    ECHO = "echo"
    BRIDGE = "bridge"
    AI_GATEWAY = "ai_gateway"


#: Postgres enum type backing ``bots.bot_type``. ``create_type=True`` lets
#: ``Base.metadata.create_all`` (dev) create it; Alembic migration ``0002``
#: creates it explicitly for the real schema.
_bot_type_enum = ENUM(
    BotType.GENERIC,
    BotType.HELLO,
    BotType.ECHO,
    BotType.BRIDGE,
    BotType.AI_GATEWAY,
    name="bot_type",
    create_type=True,
    sort_order=False,
)


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

    #: Typed category — drives the flow-engine dispatch (defaults to generic).
    bot_type: Mapped[BotType] = mapped_column(
        _bot_type_enum, default=BotType.GENERIC, nullable=False
    )

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
            f"<Bot id={self.id} tg={self.telegram_bot_id} @{self.username} "
            f"active={self.is_active} type={self.bot_type}>"
        )


class DashboardAuthToken(Base):
    """A short-lived bearer credential that opens the bot-settings dashboard.

    .. deprecated::
       Superseded by Telegram WebApp ``initData`` authentication
       (:mod:`tme.services.auth`) — the dashboard is a Mini App and needs no
       bearer tokens. The table is kept for migration history until S0.3
       (Secret Vault) consolidates credential storage; no code writes to it.
    """

    __tablename__ = "dashboard_auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    bot_id: Mapped[int] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), index=True, nullable=False
    )
    bot: Mapped[Bot] = relationship()
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<DashboardAuthToken id={self.id} bot={self.bot_id} owner={self.owner_telegram_id}>"


class BotConfig(Base):
    """The JSON flow / behaviour configuration for one :class:`Bot`.

    ``flow`` is stored as JSONB and validated against the per-type config schema
    (see :mod:`tme.schemas.bot_config`) at the service layer.
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

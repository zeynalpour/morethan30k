"""Initial schema: users, bots, bot_configs.

Hand-written equivalent of ``alembic revision --autogenerate -m "initial schema"``
against :mod:`tme.database.models` (``User`` 1─* ``Bot`` 1─1 ``BotConfig``),
including the shared ``id`` / ``created_at`` / ``updated_at`` columns every
model inherits from :class:`tme.database.base.Base`.

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- users ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # --- bots ----------------------------------------------------------------
    op.create_table(
        "bots",
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("webhook_registered", sa.Boolean(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_bots_token"), "bots", ["token"], unique=True)
    op.create_index(op.f("ix_bots_telegram_bot_id"), "bots", ["telegram_bot_id"], unique=True)
    op.create_index(op.f("ix_bots_owner_id"), "bots", ["owner_id"], unique=False)

    # --- bot_configs -----------------------------------------------------------
    op.create_table(
        "bot_configs",
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("flow", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["bot_id"],
            ["bots.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_bot_configs_bot_id"), "bot_configs", ["bot_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_configs_bot_id"), table_name="bot_configs")
    op.drop_table("bot_configs")

    op.drop_index(op.f("ix_bots_owner_id"), table_name="bots")
    op.drop_index(op.f("ix_bots_telegram_bot_id"), table_name="bots")
    op.drop_index(op.f("ix_bots_token"), table_name="bots")
    op.drop_table("bots")

    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")

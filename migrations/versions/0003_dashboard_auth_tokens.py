"""Add dashboard_auth_tokens — short-lived bearer credentials for the settings UI.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_auth_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dashboard_auth_tokens_token_hash"),
        "dashboard_auth_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_dashboard_auth_tokens_bot_id"),
        "dashboard_auth_tokens",
        ["bot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dashboard_auth_tokens_owner_telegram_id"),
        "dashboard_auth_tokens",
        ["owner_telegram_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_dashboard_auth_tokens_owner_telegram_id"), table_name="dashboard_auth_tokens"
    )
    op.drop_index(op.f("ix_dashboard_auth_tokens_bot_id"), table_name="dashboard_auth_tokens")
    op.drop_index(op.f("ix_dashboard_auth_tokens_token_hash"), table_name="dashboard_auth_tokens")
    op.drop_table("dashboard_auth_tokens")

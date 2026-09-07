"""Add bots.bot_type — typed bot categories (S0.2).

Introduces the ``bot_type`` Postgres enum and a ``bots.bot_type`` column so each
row carries a typed category that the flow engine dispatches on (generic |
hello | echo | bridge | ai_gateway). Existing rows are backfilled to ``generic``
so the move is non-breaking; the column is then made NOT NULL.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_bot_type = postgresql.ENUM(
    "generic",
    "hello",
    "echo",
    "bridge",
    "ai_gateway",
    name="bot_type",
    create_type=True,
)


def upgrade() -> None:
    _bot_type.create(op.get_bind(), checkfirst=True)

    # Add as nullable, backfill to the safe default, then enforce NOT NULL.
    op.add_column("bots", sa.Column("bot_type", _bot_type, nullable=True))
    op.execute("UPDATE bots SET bot_type = 'generic' WHERE bot_type IS NULL")
    with op.batch_alter_table("bots") as batch_op:
        batch_op.alter_column("bot_type", nullable=False, server_default=sa.text("'generic'"))


def downgrade() -> None:
    with op.batch_alter_table("bots") as batch_op:
        batch_op.drop_column("bot_type")
    _bot_type.drop(op.get_bind(), checkfirst=True)

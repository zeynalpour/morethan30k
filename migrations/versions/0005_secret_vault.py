"""Secret Vault — secrets table + bots.token_hash (Phase 0, S0.3).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09

Two changes:

1. New ``secrets`` table — envelope-encrypted material (AES-GCM payload +
   wrapped DEK), keyed uniquely by (kind, ref_id).
2. New ``bots.token_hash`` — peppered HMAC of the routing token. The
   webhook hot path resolves bots by this hash; ``bots.token`` itself is
   migrated into the vault and cleared in a follow-up data migration once
   every row has a vault copy (both columns coexist during the transition,
   per docs/architecture/04-security.md § Secrets).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("last_four", sa.String(length=8), nullable=True),
        sa.Column(
            "rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "ref_id", name="uq_secrets_kind_ref"),
    )
    op.create_index("ix_secrets_kind", "secrets", ["kind"])
    op.create_index("ix_secrets_ref_id", "secrets", ["ref_id"])

    # Hashed routing key for bots: unique (like token), NULLable during
    # the migration window so existing rows don't break the unique index.
    op.add_column("bots", sa.Column("token_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_bots_token_hash", "bots", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_bots_token_hash", table_name="bots")
    op.drop_column("bots", "token_hash")
    op.drop_index("ix_secrets_ref_id", table_name="secrets")
    op.drop_index("ix_secrets_kind", table_name="secrets")
    op.drop_table("secrets")

"""Vault activation — bots.token nullable + the missing secrets timestamps (S0.3, #28).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14

Two schema changes, both prerequisites for actually switching a stack over to
the vault (issue #28):

1. ``bots.token`` becomes **nullable**. Schema only, no data movement — the
   plaintext column keeps its contents and its unique index; being nullable is
   what lets the backfill job (``scripts/vault_backfill.py``) clear a row
   *after* it has verified that the vaulted copy decrypts back to the same
   value. Clearing the column is deliberately NOT done here: migrations run on
   container start, so a data change would silently touch every stack
   (dev/test/prod) at once. Data changes are per stack, run by the owner, and
   verified.
2. ``secrets.created_at`` / ``secrets.updated_at`` are added. Every model
   inherits those two columns from :class:`tme.database.base.Base`, but
   revision 0005 created the ``secrets`` table without them — so every ORM
   ``SELECT`` against ``secrets`` failed with ``UndefinedColumnError`` and the
   vault could not store or read a single row. Latent since 0005 (nothing
   exercised the vault: no stack had a master key, so provisioning skipped
   vaulting entirely); the first real hash/vault round-trip surfaced it. The
   backfill can neither vault nor verify without a working vault read/write,
   so it is fixed here, in the same sub-phase, per the schema-first rule.

Lifecycle of ``bots.token``: nullable (this revision) → populated until a stack
is backfilled (script) → cleared per stack → column dropped in a later release,
once dev/test/prod are all backfilled and verified.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "bots",
        "token",
        existing_type=sa.String(length=128),
        existing_nullable=False,
        nullable=True,
    )

    # Server defaults backfill existing rows, so both columns can be NOT NULL.
    op.add_column(
        "secrets",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "secrets",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("secrets", "updated_at")
    op.drop_column("secrets", "created_at")

    # Fails (correctly) if a stack has already run the backfill with
    # --clear-plaintext: NULL tokens cannot be restored without the vault.
    op.alter_column(
        "bots",
        "token",
        existing_type=sa.String(length=128),
        existing_nullable=True,
        nullable=False,
    )

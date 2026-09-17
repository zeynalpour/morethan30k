"""Vault backfill — move every tenant token into the secret vault (S0.3, #28).

Per stack, once, by the owner: this is a **data** change, deliberately kept out
of the migrations so a container start can never touch every stack at once.

What it does, per bot, in this order:

1. sets ``bots.token_hash`` if it is missing (or does not match the token —
   the hash is derived data, so it is safe to recompute; per row this is
   also what a ``VAULT_PEPPER`` rotation needs);
2. vaults the token if no vault row exists for the bot;
3. **verifies** the vaulted copy by decrypting it and comparing to the
   plaintext — a mismatch aborts the run before anything is cleared;
4. only with ``--clear-plaintext`` (and only after every row verified):
   sets ``bots.token`` to NULL.

For a row whose plaintext is already NULL (a previous run, or a pepper
rotation afterwards) there is nothing to compare against, so the vaulted
token becomes the reference: the hash is re-derived from it and repaired if
it no longer matches. A row with neither a plaintext nor a vault copy aborts
the run — it cannot be repaired.

Rollout order per stack: set ``VAULT_MASTER_KEY`` + ``VAULT_PEPPER``, run the
dry run, run ``--apply``, confirm, then ``--apply --clear-plaintext``. The
plaintext column is dropped in a later release, once dev/test/prod are done.

Usage::

    uv run python scripts/vault_backfill.py                    # dry run (default)
    uv run python scripts/vault_backfill.py --apply            # hash + vault
    uv run python scripts/vault_backfill.py --apply --clear-plaintext

The vaulted value is authoritative: a vault row that does not decrypt back to
the plaintext is reported and aborts the run (never silently overwritten —
one of the two copies is stale and only a human can decide which).

Idempotent: a second ``--apply`` run finds every hash set and every token
vaulted, so it writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.core.logging import get_logger
from tme.database.engine import session_scope
from tme.database.models import Bot
from tme.services.vault import (
    _master_key,
    has_bot_token,
    load_bot_token,
    store_bot_token,
    token_hash,
)

logger = get_logger(__name__)

#: Rows handled per transaction — a big stack commits as it goes instead of
#: holding one giant transaction open for the whole run.
DEFAULT_BATCH_SIZE = 500

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_USAGE = 2


@dataclass
class Summary:
    """Per-run counters (also the operator-facing report)."""

    scanned: int = 0
    hash_set: int = 0
    vaulted: int = 0
    verified: int = 0
    cleared: int = 0
    failed: int = 0

    def lines(self) -> list[str]:
        return [
            f"scanned  {self.scanned}",
            f"hash-set {self.hash_set}",
            f"vaulted  {self.vaulted}",
            f"verified {self.verified}",
            f"cleared  {self.cleared}",
            f"failed   {self.failed}",
        ]


class VaultBackfillError(Exception):
    """A row could not be verified; nothing was cleared."""


async def _process_bot(
    session: AsyncSession, bot: Bot, *, apply: bool, summary: Summary
) -> int | None:
    """Hash + vault + verify one bot; return its id when cleared-eligible.

    Raises:
        VaultBackfillError: the row cannot be verified (or has nothing left
            to verify from), so the run must stop before any clearing.
    """
    summary.scanned += 1
    plaintext = bot.token
    expected = token_hash(plaintext) if plaintext is not None else None

    if plaintext is None:
        # Already cleared by an earlier run: the vault is the ONLY copy, so the
        # hash can only be checked/repaired FROM it. This is the path a
        # post-backfill VAULT_PEPPER rotation lands on.
        vaulted = await load_bot_token(session, bot_id=bot.id)
        if vaulted is None:
            summary.failed += 1
            raise VaultBackfillError(
                f"bot id={bot.id} has no plaintext token and no vaulted copy — "
                "irrecoverable without a backup; restore this stack before continuing"
            )
        if bot.token_hash != token_hash(vaulted):
            logger.warning(
                "bots.token_hash stale for bot id=%s (plaintext already cleared) — "
                "repairing it from the vaulted token",
                bot.id,
            )
            if apply:
                bot.token_hash = token_hash(vaulted)
            summary.hash_set += 1
        summary.verified += 1
        return None

    if bot.token_hash != expected:
        logger.warning(
            "bots.token_hash %s for bot id=%s — setting it from the plaintext",
            "missing" if bot.token_hash is None else "does not match (pepper rotated?)",
            bot.id,
        )
        if apply:
            bot.token_hash = expected
        summary.hash_set += 1

    has_vault_row = await has_bot_token(session, bot_id=bot.id)
    if not has_vault_row:
        if apply:
            await store_bot_token(session, bot_id=bot.id, token=plaintext)
            # autoflush is off on the app's session factory: flush explicitly so
            # the verification read below sees the row we just wrote.
            await session.flush()
        summary.vaulted += 1

    if not apply and not has_vault_row:
        # Dry run: a row that would only be vaulted on --apply has nothing to
        # compare against yet, so it is not counted as verified.
        return None

    vaulted = await load_bot_token(session, bot_id=bot.id)
    if vaulted != plaintext:
        summary.failed += 1
        raise VaultBackfillError(
            f"bot id={bot.id}: vault round-trip mismatch (decrypted value != plaintext)"
        )
    summary.verified += 1
    return bot.id if apply else None


async def _clear_plaintext(bot_ids: list[int], *, batch_size: int) -> int:
    """NULL the plaintext token for every verified bot id (phase 2)."""
    cleared = 0
    for start in range(0, len(bot_ids), batch_size):
        chunk = bot_ids[start : start + batch_size]
        async with session_scope() as session:
            rows = await session.execute(select(Bot).where(Bot.id.in_(chunk)))
            for bot in rows.scalars():
                bot.token = None
                cleared += 1
    return cleared


async def run_backfill(
    *,
    apply: bool = False,
    clear_plaintext: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Summary:
    """Hash + vault every bot token; clear the plaintext only when asked.

    Raises:
        VaultBackfillError: any verification failure — nothing is cleared.
        ValueError: ``clear_plaintext`` without ``apply`` (there is nothing to
            clear when nothing was written), or the master key is unusable.
    """
    if clear_plaintext and not apply:
        raise ValueError("--clear-plaintext only makes sense together with --apply")
    # Fail fast and loudly: without a usable master key the job can neither
    # vault nor verify anything (and must never clear).
    _master_key()

    summary = Summary()
    clearable: list[int] = []

    last_id = 0
    while True:
        async with session_scope() as session:
            batch = list(
                (
                    await session.execute(
                        select(Bot).where(Bot.id > last_id).order_by(Bot.id).limit(batch_size)
                    )
                ).scalars()
            )
            if not batch:
                break
            last_id = batch[-1].id
            for bot in batch:
                bot_id = await _process_bot(session, bot, apply=apply, summary=summary)
                if bot_id is not None:
                    clearable.append(bot_id)

    # Phase 2 — only after EVERY row verified, and only if explicitly asked.
    if apply and clear_plaintext:
        summary.cleared = await _clear_plaintext(clearable, batch_size=batch_size)

    return summary


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vault_backfill",
        description="Vault every tenant bot token and (optionally) clear the plaintext column.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default is a dry run that writes nothing)",
    )
    parser.add_argument(
        "--clear-plaintext",
        action="store_true",
        help="NULL bots.token after every round-trip verified; requires --apply",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per transaction (default {DEFAULT_BATCH_SIZE})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — returns the process exit code."""
    args = _parse_args(argv)
    if args.clear_plaintext and not args.apply:
        print(
            "error: --clear-plaintext is only honoured together with --apply "
            "(a dry run writes nothing, so there is nothing to clear)",
            file=sys.stderr,
        )
        return _EXIT_USAGE

    mode = "--apply" if args.apply else "dry run (no writes)"
    if args.apply and args.clear_plaintext:
        mode += " + --clear-plaintext"
    print(f"vault backfill [{mode}]")

    try:
        summary = asyncio.run(
            run_backfill(
                apply=args.apply,
                clear_plaintext=args.clear_plaintext,
                batch_size=args.batch_size,
            )
        )
    except VaultBackfillError as exc:
        print(f"\nABORTED: {exc}", file=sys.stderr)
        print("No plaintext token was cleared.", file=sys.stderr)
        return _EXIT_FAILED
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_FAILED

    print("\nsummary:")
    for line in summary.lines():
        print(f"  {line}")
    if not args.apply:
        print("\ndry run — nothing was written (rerun with --apply to persist)")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

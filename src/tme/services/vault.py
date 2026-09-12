"""Secret Vault — envelope encryption for tokens and API keys (Phase 0, S0.3).

Every secret (tenant bot token now; AI gateway API keys later) is stored
as an AES-256-GCM ciphertext in the ``secrets`` table. A per-secret random
DEK is itself encrypted (wrapped) by the master key from settings — so
rotating the master key only requires re-wrapping DEKs, never re-encrypting
every payload, and Postgres never sees plaintext.

Lookup on the webhook hot path is by ``token_hash`` — HMAC-SHA256 of the
token with a server pepper — so resolving a bot from an incoming update
never decrypts anything. Decryption happens lazily in the adapter path
(when we actually need to call Telegram with the token).

Design mirrors docs/architecture/04-security.md (§ Secrets).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import hmac
import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tme.config import settings
from tme.core.logging import get_logger
from tme.database.models import Secret

logger = get_logger(__name__)

# AES-GCM nonce size (96-bit is the standard choice).
_NONCE_SIZE = 12
# DEK size — AES-256 key.
_DEK_SIZE = 32


class SecretKind(StrEnum):
    """Categories of vault-protected material."""

    BOT_TOKEN = "bot_token"
    API_KEY = "api_key"


def _master_key() -> bytes:
    """Decode the base64 master key from settings (32 bytes for AES-256).

    Raises ``ValueError`` if unset or malformed — failing fast at first use
    beats corrupting ciphertexts with a wrong key length.
    """
    raw = settings.vault_master_key.get_secret_value() if settings.vault_master_key else ""
    if not raw:
        raise ValueError("VAULT_MASTER_KEY is not set — the vault cannot operate")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError(f"VAULT_MASTER_KEY is not valid base64: {exc}") from exc
    if len(key) != _DEK_SIZE:
        raise ValueError(
            f"VAULT_MASTER_KEY must decode to exactly 32 bytes for AES-256, got {len(key)}"
        )
    return key


def token_hash(token: str) -> str:
    """Peppered, constant-length HMAC of a bot token.

    This is the value stored on ``bots.token_hash`` and the lookup key on
    the hot path — it reveals nothing about the token itself (unreversible
    without the pepper) and compares in constant time via the DB index.
    """
    pepper = settings.vault_pepper.get_secret_value() if settings.vault_pepper else ""
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def _wrap(dek: bytes, master: bytes) -> bytes:
    """Encrypt a DEK with the master key (AES-GCM, fresh nonce)."""
    nonce = os.urandom(_NONCE_SIZE)
    return nonce + AESGCM(master).encrypt(nonce, dek, None)


def _unwrap(blob: bytes, master: bytes) -> bytes:
    """Decrypt a DEK produced by :func:`_wrap`."""
    nonce, ciphertext = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(master).decrypt(nonce, ciphertext, None)


def _encrypt(dek: bytes, plaintext: bytes) -> bytes:
    """Encrypt a payload with a DEK (AES-GCM, fresh nonce)."""
    nonce = os.urandom(_NONCE_SIZE)
    return nonce + AESGCM(dek).encrypt(nonce, plaintext, None)


def _decrypt(dek: bytes, blob: bytes) -> bytes:
    """Decrypt a payload produced by :func:`_encrypt`."""
    nonce, ciphertext = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(dek).decrypt(nonce, ciphertext, None)


def encrypt_secret(plaintext: str) -> tuple[bytes, bytes]:
    """Envelope-encrypt ``plaintext`` → ``(ciphertext, wrapped_dek)``.

    Both blobs are opaque; only the pair (plus the master key) recovers
    the secret. Nonces are random per call, so encrypting the same value
    twice yields different ciphertexts.
    """
    master = _master_key()
    dek = os.urandom(_DEK_SIZE)
    return _encrypt(dek, plaintext.encode()), _wrap(dek, master)


def decrypt_secret(ciphertext: bytes, wrapped_dek: bytes) -> str:
    """Recover plaintext from an envelope pair. Raises if tampered."""
    master = _master_key()
    dek = _unwrap(wrapped_dek, master)
    return _decrypt(dek, ciphertext).decode()


async def store_secret(
    session: AsyncSession,
    *,
    kind: SecretKind,
    ref_id: str,
    plaintext: str,
    last_four: str | None = None,
) -> Secret:
    """Insert (or replace) a vault row for ``(kind, ref_id)``.

    ``ref_id`` identifies what the secret belongs to (a bot id, an owner
    id…). Same-pair inserts replace the previous row — one live secret
    per slot keeps the vault simple; history is not needed for tokens.
    """
    ciphertext, wrapped_dek = encrypt_secret(plaintext)
    display = last_four if last_four is not None else plaintext[-4:]
    result = await session.execute(
        select(Secret).where(Secret.kind == kind, Secret.ref_id == ref_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = Secret(
            kind=kind,
            ref_id=ref_id,
            ciphertext=ciphertext,
            wrapped_dek=wrapped_dek,
            last_four=display,
        )
        session.add(row)
    else:
        row.ciphertext = ciphertext
        row.wrapped_dek = wrapped_dek
        row.last_four = display
    row.rotated_at = datetime.now(UTC)
    return row


async def load_secret(session: AsyncSession, *, kind: SecretKind, ref_id: str) -> str | None:
    """Return the decrypted secret for ``(kind, ref_id)``, or ``None``."""
    result = await session.execute(
        select(Secret).where(Secret.kind == kind, Secret.ref_id == ref_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return decrypt_secret(row.ciphertext, row.wrapped_dek)


# --- Bot-token specialisation -----------------------------------------------


async def store_bot_token(session: AsyncSession, *, bot_id: int, token: str) -> Secret:
    """Store a tenant bot's token in the vault, keyed by bot id."""
    return await store_secret(
        session, kind=SecretKind.BOT_TOKEN, ref_id=str(bot_id), plaintext=token
    )


async def load_bot_token(session: AsyncSession, *, bot_id: int) -> str | None:
    """Return a tenant bot's decrypted token, or ``None`` if not vaulted."""
    return await load_secret(session, kind=SecretKind.BOT_TOKEN, ref_id=str(bot_id))


def new_api_key() -> str:
    """Generate a fresh API key value (for Phase 7 public API keys)."""
    return f"tme_{uuid.uuid4().hex}"

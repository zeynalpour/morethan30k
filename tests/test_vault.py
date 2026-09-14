"""Secret Vault tests (Phase 0, S0.3).

Pure-crypto tests run everywhere; the DB round-trip uses the integration
stack when it's up (mirroring test_integration.py's skip pattern).
"""

from __future__ import annotations

import asyncio
import base64
import os

from pydantic import SecretStr
import pytest

from tme.config import settings
from tme.database.models import Bot
from tme.services import vault


def _set_vault(monkeypatch: pytest.MonkeyPatch, *, key: str | None, pepper: str | None) -> None:
    """Point the settings singleton at throwaway vault material."""
    monkeypatch.setattr(settings, "vault_master_key", SecretStr(key) if key else None)
    monkeypatch.setattr(settings, "vault_pepper", SecretStr(pepper) if pepper else None)


def _valid_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class _EmptyResult:
    """A result set with no rows (no DB needed for the accessor's fallbacks)."""

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return iter(())


class _EmptySession:
    """Stand-in for AsyncSession whose every query matches nothing."""

    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


class _VaultRow:
    """A ``secrets`` row as the accessor sees it."""

    def __init__(self, ref_id: str, ciphertext: bytes, wrapped_dek: bytes) -> None:
        self.ref_id = ref_id
        self.ciphertext = ciphertext
        self.wrapped_dek = wrapped_dek


class _SingleRowSession:
    """Stand-in session whose Secret query returns exactly one row."""

    def __init__(self, row: _VaultRow) -> None:
        self._row = row

    async def execute(self, *_args, **_kwargs):
        row = self._row

        class _Result:
            def scalars(self):
                return iter([row])

            def scalar_one_or_none(self):
                return row

        return _Result()


def _bot_row(*, bot_id: int = 7, token: str | None = "123:PLAINTEXT") -> Bot:
    """An in-memory Bot row (no session needed for the accessor's fallbacks)."""
    return Bot(id=bot_id, token=token, telegram_bot_id=123, owner_id=1)


class TestCrypto:
    """Envelope encryption primitives — no DB involved."""

    def test_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        ct, wdek = vault.encrypt_secret("123456:ABC-DEF-very-secret")
        assert b"ABC-DEF" not in ct and b"ABC-DEF" not in wdek
        assert vault.decrypt_secret(ct, wdek) == "123456:ABC-DEF-very-secret"

    def test_ciphertext_is_nondeterministic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        ct1, _ = vault.encrypt_secret("same-secret")
        ct2, _ = vault.encrypt_secret("same-secret")
        assert ct1 != ct2, "fresh nonces must make ciphertexts differ"

    def test_wrong_master_key_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        ct, wdek = vault.encrypt_secret("secret")
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        with pytest.raises(Exception):  # noqa: B017 - InvalidTag, any crypto error
            vault.decrypt_secret(ct, wdek)

    def test_tampered_ciphertext_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        ct, wdek = vault.encrypt_secret("secret")
        bad = bytearray(ct)
        bad[-1] ^= 0xFF
        with pytest.raises(Exception):  # noqa: B017 - InvalidTag on tamper
            vault.decrypt_secret(bytes(bad), wdek)

    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            (None, "unset"),
            ("not-base64!!", "malformed"),
            (base64.b64encode(b"short").decode(), "wrong length"),
        ],
    )
    def test_bad_master_key_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, raw: str | None, why: str
    ) -> None:
        _set_vault(monkeypatch, key=raw, pepper="p")
        with pytest.raises(ValueError, match="VAULT_MASTER_KEY"):
            vault.encrypt_secret("x")


class TestTokenHash:
    """Peppered HMAC routing hashes."""

    def test_deterministic_and_hex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="fixed-pepper")
        h1 = vault.token_hash("123456:ABC")
        h2 = vault.token_hash("123456:ABC")
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_pepper_changes_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="pepper-a")
        ha = vault.token_hash("123456:ABC")
        _set_vault(monkeypatch, key=None, pepper="pepper-b")
        hb = vault.token_hash("123456:ABC")
        assert ha != hb

    def test_token_not_recoverable_from_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="p")
        h = vault.token_hash("123456:super-secret-token")
        assert "super-secret-token" not in h
        assert "123456" not in h

    def test_unique_per_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="p")
        assert vault.token_hash("111:AAA") != vault.token_hash("222:BBB")


class TestVaultFallback:
    """Provisioning must survive (and warn) without a master key."""

    def test_vault_status_off_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="p")
        assert vault.vault_available() is False

    def test_vault_status_on_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        assert vault.vault_available() is True


class TestTokenAccessor:
    """The one vault-first accessor (S0.3 activation, issue #28)."""

    def test_prefers_the_vault_over_the_plaintext_column(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A vaulted token wins: the database column is only a fallback.

        Stubs the session with a real envelope pair (no DB), so the preference
        is provable without the integration stack.
        """
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        ciphertext, wrapped_dek = vault.encrypt_secret("123:VAULTED")
        session = _SingleRowSession(_VaultRow("7", ciphertext, wrapped_dek))

        bot = _bot_row(token="123:STALE-PLAINTEXT")
        assert asyncio.run(vault.resolve_bot_token(session, bot)) == "123:VAULTED"

    def test_falls_back_to_plaintext_when_not_vaulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        bot = _bot_row()
        assert asyncio.run(vault.resolve_bot_token(_EmptySession(), bot)) == "123:PLAINTEXT"

    def test_falls_back_to_plaintext_without_a_master_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key → no vault query, no crash, plaintext column as today."""
        _set_vault(monkeypatch, key=None, pepper="p")
        bot = _bot_row()
        assert asyncio.run(vault.resolve_bot_token(_EmptySession(), bot)) == "123:PLAINTEXT"

    def test_undecryptable_vault_row_falls_back_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row that no longer decrypts must not break the send path."""
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        session = _SingleRowSession(_VaultRow("7", b"\x00" * 40, b"\x00" * 40))
        bot = _bot_row()
        assert asyncio.run(vault.resolve_bot_token(session, bot)) == "123:PLAINTEXT"

    def test_batch_form_resolves_every_bot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=None, pepper="p")
        bots = [_bot_row(bot_id=1, token="1:a"), _bot_row(bot_id=2, token=None)]
        assert asyncio.run(vault.resolve_bot_tokens(_EmptySession(), bots)) == {1: "1:a", 2: None}

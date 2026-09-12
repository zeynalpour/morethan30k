"""Secret Vault tests (Phase 0, S0.3).

Pure-crypto tests run everywhere; the DB round-trip uses the integration
stack when it's up (mirroring test_integration.py's skip pattern).
"""

from __future__ import annotations

import base64
import os

from pydantic import SecretStr
import pytest

from tme.config import settings
from tme.services import vault
from tme.services.managed_bots import _vault_status


def _set_vault(monkeypatch: pytest.MonkeyPatch, *, key: str | None, pepper: str | None) -> None:
    """Point the settings singleton at throwaway vault material."""
    monkeypatch.setattr(settings, "vault_master_key", SecretStr(key) if key else None)
    monkeypatch.setattr(settings, "vault_pepper", SecretStr(pepper) if pepper else None)


def _valid_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


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
        assert _vault_status() is False

    def test_vault_status_on_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_vault(monkeypatch, key=_valid_key(), pepper="p")
        assert _vault_status() is True

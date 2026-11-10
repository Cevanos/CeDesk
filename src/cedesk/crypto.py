"""Secrets kept in the database (md 7.1: the Cevanos API key and webhook secret are "stored
encrypted, never in config or a tenant-readable table, and never logged").

AES-256-GCM. The key is derived from `CEDESK_SECRET_KEY` with HKDF, so there is no second
secret to manage; changing `CEDESK_SECRET_KEY` means entering these secrets again.

Sealed form: 1 version byte, 12-byte nonce, then the ciphertext with its tag. The row's id
is bound in as associated data, so a sealed value copied into another row does not open.
"""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = b"\x01"
NONCE_BYTES = 12
_INFO = b"cedesk/sealed-secrets/v1"


class CannotOpen(ValueError):
    """Sealed with another key (CEDESK_SECRET_KEY changed), for another row, or damaged."""


class Sealer:
    def __init__(self, secret_key: str) -> None:
        if len(secret_key) < 16:
            raise ValueError("The secret key is too short to derive an encryption key.")
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
            secret_key.encode()
        )
        self._aead = AESGCM(key)

    def seal(self, value: str, *, bound_to: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return VERSION + nonce + self._aead.encrypt(nonce, value.encode(), bound_to.encode())

    def open(self, sealed: bytes, *, bound_to: str) -> str:
        if len(sealed) <= 1 + NONCE_BYTES or sealed[:1] != VERSION:
            raise CannotOpen("Not a sealed value.")
        nonce, body = sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, body, bound_to.encode()).decode()
        except InvalidTag as exc:
            raise CannotOpen(
                "This secret can't be read: CEDESK_SECRET_KEY changed since it was saved. "
                "Enter it again."
            ) from exc

"""Argon2id password hashing."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified against when the workspace or email does not exist, so a wrong
# email takes as long as a wrong password and reveals nothing.
_DUMMY_HASH = _hasher.hash("cedesk-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify(password_hash: str | None, password: str) -> tuple[bool, str | None]:
    """(matches, new_hash). `new_hash` is set when the stored hash uses old parameters."""
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if password_hash is None:
        return False, None
    new_hash = _hasher.hash(password) if _hasher.check_needs_rehash(password_hash) else None
    return True, new_hash

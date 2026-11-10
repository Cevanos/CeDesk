"""The `Idempotency-Key` header (md 2B): a repeat of a POST returns what the first one made.
No database."""

import hashlib
import re
from datetime import timedelta

LIFETIME = timedelta(hours=24)
"""A key can be reused for a new request after this."""
KEY = re.compile(r"^[\x21-\x7e]{1,255}$")  # printable ASCII, no spaces


class BadKey(ValueError):
    """Not a usable key."""


def check_key(raw: str) -> str:
    if not KEY.match(raw):
        raise BadKey("Idempotency-Key is 1-255 printable characters without spaces.")
    return raw


def fingerprint(scope: str, *parts: bytes) -> bytes:
    """What the request was: the route (with its ids) and the body. Two requests with the
    same key and a different fingerprint are a client bug, and the second is refused."""
    h = hashlib.sha256(scope.encode())
    for part in parts:
        h.update(len(part).to_bytes(8, "big"))
        h.update(part)
    return h.digest()

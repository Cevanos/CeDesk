"""Two-step sign-in with an authenticator app (md S2). Pure rules.

TOTP as in RFC 6238 with the settings every authenticator app uses by default: SHA-1,
6 digits, 30-second steps. A code from the step before or after also works (clocks
drift), but each step works only once, so a code seen over someone's shoulder can't be
used again. Recovery codes: 10 one-time codes of 10 characters, kept only as hashes.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODES = 10
# How long a password-only session waits for the code before the member signs in again.
PENDING_LIFETIME = timedelta(minutes=10)
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


def new_secret() -> str:
    """160 random bits, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def step_of(now: datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def matching_step(secret: str, code: str, now: datetime, last_step: int) -> int | None:
    """The step the code belongs to, if it is right and newer than `last_step`."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = step_of(now)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, *, account: str, issuer: str) -> str:
    """For the QR code the app scans."""
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def new_recovery_codes() -> list[str]:
    """Shown once, as XXXXX-XXXXX."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def looks_like_recovery_code(code: str) -> bool:
    return len(_plain(code)) == 10


def _plain(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def recovery_hash(code: str) -> bytes:
    """50 random bits per code, one use each, and wrong tries count toward the sign-in
    lockout, so a plain SHA-256 is enough."""
    return hashlib.sha256(_plain(code).encode()).digest()

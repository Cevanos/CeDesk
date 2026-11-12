"""TOTP against RFC 6238's own test vectors, and the one-use rule."""

import base64
from datetime import UTC, datetime

from cedesk.helpdesk.domain import two_factor as t

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


def test_rfc_6238_vectors() -> None:
    # The RFC lists 8 digits; authenticator apps show the last 6.
    for seconds, expected in [(59, "287082"), (1111111109, "081804"), (2000000000, "279037")]:
        assert t.code_at(RFC_SECRET, seconds // 30) == expected


def test_a_code_works_once_and_only_near_now() -> None:
    now = datetime.fromtimestamp(1111111109, UTC)
    step = t.step_of(now)
    assert t.matching_step(RFC_SECRET, "081 804", now, 0) == step
    assert t.matching_step(RFC_SECRET, "081804", now, step) is None  # used already
    assert t.matching_step(RFC_SECRET, t.code_at(RFC_SECRET, step + 1), now, step) == step + 1
    assert t.matching_step(RFC_SECRET, t.code_at(RFC_SECRET, step + 2), now, 0) is None
    assert t.matching_step(RFC_SECRET, "12345", now, 0) is None


def test_recovery_codes() -> None:
    codes = t.new_recovery_codes()
    assert len(set(codes)) == 10
    assert t.recovery_hash(codes[0]) == t.recovery_hash(codes[0].lower().replace("-", " "))
    assert t.looks_like_recovery_code(codes[0]) and not t.looks_like_recovery_code("123456")

"""Retry timing for outbox delivery, and Idempotency-Key rules (no database)."""

from uuid import UUID

import pytest

from cedesk.helpdesk.domain import delivery, idempotency


def test_retry_schedule() -> None:
    assert [delivery.retry_after(n) for n in range(1, 7)] == [60, 300, 1800, 7200, 43200, None]
    with pytest.raises(ValueError):
        delivery.retry_after(0)


def test_effect_key_is_stable_per_handler() -> None:
    event = UUID("00000000-0000-0000-0000-000000000001")
    assert delivery.effect_key(event, "cevanos") == f"{event}:cevanos"
    assert delivery.effect_key(event, "cevanos") != delivery.effect_key(event, "webhooks")


@pytest.mark.parametrize("key", ["a", "550e8400-e29b-41d4-a716-446655440000", "x" * 255])
def test_good_keys(key: str) -> None:
    assert idempotency.check_key(key) == key


@pytest.mark.parametrize("key", ["", "has space", "x" * 256, "tab\there", "ünï"])
def test_bad_keys(key: str) -> None:
    with pytest.raises(idempotency.BadKey):
        idempotency.check_key(key)


def test_fingerprint_tells_requests_apart() -> None:
    fp = idempotency.fingerprint
    assert fp("POST /tickets", b"{}") == fp("POST /tickets", b"{}")
    assert fp("POST /tickets", b"{}") != fp("POST /tickets/1/messages", b"{}")
    # Parts are length-framed: moving bytes between parts is a different request.
    assert fp("s", b"ab", b"c") != fp("s", b"a", b"bc")
    assert len(fp("s")) == 32

"""Card-number redaction (md I5): 13-19 digits that pass Luhn, before saving."""

import time

import pytest

from cedesk.helpdesk.domain.redaction import REPLACEMENT, line, luhn, redact, redact_numbers

R = REPLACEMENT


@pytest.mark.parametrize(
    ("text", "expected", "count"),
    [
        ("card 4111 1111 1111 1111 cvv 123", f"card {R} cvv 123", 1),
        ("4111-1111-1111-1111", R, 1),
        ("4111111111111111", R, 1),
        ("4111 1111 1111 1111 123", f"{R} 123", 1),  # the CVV right after stays
        ("Amex 3782 822463 10005.", f"Amex {R}.", 1),  # 15 digits
        ("two: 4111111111111111 and 5500 0000 0000 0004", f"two: {R} and {R}", 2),
        ("order 41111111111111111234", "order 41111111111111111234", 0),  # 20 digits
        ("4111 1111 1111 1112", "4111 1111 1111 1112", 0),  # fails Luhn
        ("phone +91 98765 43210", "phone +91 98765 43210", 0),
        ("4111  1111 1111 1111", "4111  1111 1111 1111", 0),  # two spaces: not one number
        ("४१११ ११११ ११११ ११११", "४१११ ११११ ११११ ११११", 0),  # ASCII digits only
    ],
)
def test_redact(text: str, expected: str, count: int) -> None:
    assert redact(text) == (expected, count)


def test_luhn() -> None:
    assert luhn("4111111111111111") and luhn("79927398713")
    assert not luhn("4111111111111112")


def test_the_line_says_how_many() -> None:
    assert line(1) == "1 card number removed from the customer's message"
    assert line(3) == "3 card numbers removed from the customer's message"


def test_a_long_run_of_digit_groups_is_quick() -> None:
    started = time.perf_counter()
    redact("1 " * 25_000)  # a 50,000-character message
    assert time.perf_counter() - started < 3


def test_the_numbers_removed_are_given_back_as_digits() -> None:
    out, numbers = redact_numbers("a 4111-1111-1111-1111 b 4111111111111111 c")
    assert out == f"a {R} b {R} c"
    assert numbers == ["4111111111111111", "4111111111111111"]  # the same card twice

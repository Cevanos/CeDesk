"""Saved reply shortcuts (md W19): how they are written and how an import avoids a clash."""

import pytest

from cedesk.helpdesk.domain.saved_replies import (
    ShortcutError,
    free_shortcut,
    normalise_shortcut,
)


@pytest.mark.parametrize(
    ("raw", "stored"),
    [("refund", "refund"), ("/Refund", "refund"), ("  /photo ", "photo"), ("a1_b-c", "a1_b-c")],
)
def test_a_shortcut_is_stored_lower_case_without_the_slash(raw: str, stored: str) -> None:
    assert normalise_shortcut(raw) == stored


@pytest.mark.parametrize("raw", ["", "/", "two words", "-dash-first", "x" * 33, "refund!", "//x"])
def test_a_bad_shortcut_is_refused(raw: str) -> None:
    with pytest.raises(ShortcutError):
        normalise_shortcut(raw)


def test_an_imported_shortcut_gets_a_number_when_taken() -> None:
    assert free_shortcut("bulk", set()) == "bulk"
    assert free_shortcut("/Bulk", {"bulk"}) == "bulk1"
    assert free_shortcut("bulk", {"bulk", "bulk1", "bulk2"}) == "bulk3"


def test_the_number_still_fits_in_32_characters() -> None:
    long = "x" * 32
    assert free_shortcut(long, {long}) == "x" * 31 + "1"
    taken = {long} | {"x" * 31 + str(n) for n in range(1, 10)}
    assert free_shortcut(long, taken) == "x" * 30 + "10"

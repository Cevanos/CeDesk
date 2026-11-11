"""Help center search words (md P6): always valid tsquery syntax, Hindi kept whole."""

import pytest

from cedesk.helpdesk.domain.articles import search_query, visitor_hash


@pytest.mark.parametrize(
    ("typed", "query"),
    [
        ("refu", "refu:*"),
        ("  Money   BACK ", "money & back:*"),
        ("refund? (late) & lost|order:*!", "refund & late & lost & order:*"),
        ("रिफंड कैसे", "रिफंड & कैसे:*"),
        ("order #1234", "order & 1234:*"),
        ("&|!:*()'\"", None),
        ("", None),
    ],
)
def test_search_query(typed: str, query: str | None) -> None:
    assert search_query(typed) == query


def test_at_most_eight_words() -> None:
    assert search_query(" ".join(f"w{n}" for n in range(20))) == (
        "w0 & w1 & w2 & w3 & w4 & w5 & w6 & w7:*"
    )


def test_visitor_ids_are_hashed() -> None:
    assert visitor_hash("abc") == visitor_hash("abc") != visitor_hash("abd")
    assert len(visitor_hash("abc")) == 32
